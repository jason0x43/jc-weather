#!/usr/bin/env python
# coding=UTF-8

import alfred
import forecastio
import glocation
import json
import os
import os.path
import re
import time
import urlparse
import wunderground
import pytz
import logging
from jcalfred import Workflow, Item, JsonFile
from datetime import date, datetime, timedelta, tzinfo
from sys import stdout


LOG = logging.getLogger(__name__)


SERVICES = {
    'wund': {
        'name': 'Weather Underground',
        'url': 'http://www.wunderground.com',
        'getkey': 'http://www.wunderground.com/weather/api/',
        'lib': wunderground
    },
    'fio': {
        'name': 'Forecast.io',
        'url': 'http://forecast.io',
        'getkey': 'https://developer.forecast.io/register',
        'lib': forecastio
    }
}

SETTINGS_VERSION = 4
DEFAULT_UNITS = 'us'
DEFAULT_ICONS = 'grzanka'
DEFAULT_TIME_FMT = '%Y-%m-%d %H:%M'
EXAMPLE_ICON = 'tstorms'
TIMESTAMP_FMT = '%Y-%m-%d %H:%M:%S'
LINE = unichr(0x2500) * 20

TIME_FORMATS = (
    DEFAULT_TIME_FMT,
    '%A, %B %d, %Y %I:%M%p',
    '%a, %d %b %Y %H:%M',
    '%I:%M%p on %m/%d/%Y',
    '%d.%m.%Y %H:%M',
    '%d/%m/%Y %H:%M',
)

FIO_TO_WUND = {
    'clear-day': 'clear',
    'clear-night': 'nt_clear',
    'partly-cloudy-day': 'partlycloudy',
    'partly-cloudy-night': 'nt_partlycloudy',
    'wind': 'hazy',
}


class LocalTimezone(tzinfo):
    '''A tzinfo object for the system timezone'''
    def __init__(self):
        self.stdoffset = timedelta(seconds=-time.timezone)
        if time.daylight:
            self.dstoffset = timedelta(seconds=-time.altzone)
        else:
            self.dstoffset = self.stdoffset
        self.dstdiff = self.dstoffset - self.stdoffset
        self.zero = timedelta(0)

    def utcoffset(self, dt):
        if self._isdst(dt):
            return self.dstoffset
        else:
            return self.stdoffset

    def dst(self, dt):
        if self._isdst(dt):
            return self.dstdiff
        else:
            return self.zero

    def tzname(self, dt):
        return time.tzname[self._isdst(dt)]

    def localize(self, dt):
        return datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute,
                        dt.second, dt.microsecond, tzinfo=self)

    def _isdst(self, dt):
        tt = (dt.year, dt.month, dt.day,
              dt.hour, dt.minute, dt.second,
              dt.weekday(), 0, 0)
        stamp = time.mktime(tt)
        tt = time.localtime(stamp)
        return tt.tm_isdst > 0


class SetupError(Exception):
    def __init__(self, title, subtitle):
        super(SetupError, self).__init__(title)
        self.title = title
        self.subtitle = subtitle


def clean_str(arg):
    return arg.replace('&', '&amp;')


LOCAL_TZ = LocalTimezone()


class WeatherWorkflow(Workflow):
    def __init__(self):
        super(WeatherWorkflow, self).__init__()
        self.cache_file = os.path.join(self.cache_dir, 'data.json')
        self._cache = None
        self._load_settings()

    @property
    def cache(self):
        if not self._cache:
            self._cache = JsonFile(self.cache_file)
        return self._cache

    def _localize_time(self, dtime=None):
        '''
        Return a datetime from the configured location adjusted for the local
        timezone.

        If no time is specified, return a localized instance of the current time.
        '''
        if dtime:
            remote_tz = pytz.timezone(self.config['location']['timezone'])
            remote_time = remote_tz.localize(dtime)
            return remote_time.astimezone(LOCAL_TZ)
        else:
            return LOCAL_TZ.localize(datetime.now())


    def _remotize_time(self, dtime=None):
        '''
        Return a time from the local timezone location adjusted for the configured
        location.

        If no time is specified, return an instance of the current time in the
        remote location's timezone.
        '''
        remote_tz = pytz.timezone(self.config['location']['timezone'])
        if dtime:
            local_time = LOCAL_TZ.localize(dtime)
            return local_time.astimezone(remote_tz)
        else:
            now = LOCAL_TZ.localize(datetime.now())
            return now.astimezone(remote_tz)

    def _migrate_settings(self):
        if 'units' in self.config:
            if self.config['units'] == 'US':
                self.config['units'] = 'us'
            else:
                self.config['units'] = 'si'

        if 'key' in self.config:
            self.config['key.wund'] = self.config['key']
            del self.config['key']

        self.config['service'] = 'wund'

        if 'name' in self.config:
            location = glocation.geocode(self.config['name'])
            name = location['name']
            short_name = name.partition(',')[0] if ',' in name else name
            self.config['location'] = {
                'name': name,
                'short_name': short_name,
                'latitude': location['latitude'],
                'longitude': location['longitude']
            }
            del self.config['name']

        if 'timezone' not in self.config['location']:
            location = self.config['location']
            tz = glocation.timezone(location['latitude'], location['longitude'])
            self.config['location']['timezone'] = tz['timeZoneId']

    def _load_settings(self):
        '''Get an the location and units to use'''
        version = self.config.get('version')
        if version is not None and version < SETTINGS_VERSION:
            self._migrate_settings()

        self.config['version'] = SETTINGS_VERSION
        self.config['units'] = self.config.get('units', DEFAULT_UNITS);
        self.config['icons'] = self.config.get('icons', DEFAULT_ICONS);
        self.config['time_format'] = self.config.get('time_format', DEFAULT_TIME_FMT);
        self.config['days'] = self.config.get('days', 3);

        import os.path
        old_config_file = os.path.join(self.data_dir, 'settings.json')
        if (os.path.exists(old_config_file) and not self.config.get('migrated')):
            old_config = JsonFile(old_config_file)
            for key, value in old_config.items():
                self.config[key] = value
            self.config['migrated'] = True

    def _validate_settings(self):
        try:
            if 'service' not in self.config:
                raise SetupError('You need to set your weather service',
                                 'Use the "wset service" command.')

            key_name = 'key.' + self.config['service']
            if key_name not in self.config:
                raise SetupError('You need to set your weather service',
                                 'Use the "wset service" command.')

            if 'location' not in self.config:
                raise SetupError('Missing default location', 'You must specify a '
                                 'default location with the "wset location" '
                                 'command')
        except SetupError, e:
            self.show_message('Error', str(e))
            raise

    def _update_location(self, query):
        '''Temporarily update the location to a new value'''
        location = glocation.geocode(query)
        name = location['name']
        short_name = name.partition(',')[0] if ',' in name else name
        tz = glocation.timezone(location['latitude'], location['longitude'])
        temp_loc = {
            'name': name,
            'short_name': short_name,
            'latitude': location['latitude'],
            'longitude': location['longitude'],
            'timezone': tz['timeZoneId']
        }
        self.config['location'].update(temp_loc)

    def _load_cached_data(self, service, location):
        data = None

        if service not in self.cache:
            self.cache[service] = {'forecasts': {}}

        if location in self.cache[service]['forecasts']:
            last_check = self.cache[service]['forecasts'][location]['requested_at']
            last_check = datetime.strptime(last_check, TIMESTAMP_FMT)
            if (datetime.now() - last_check).seconds < 300:
                data = self.cache[service]['forecasts'][location]['data']

        return data

    def _save_cached_data(self, service, location, data):
        if service not in self.cache:
            self.cache[service] = {'forecasts': {}}
        service_cache = self.cache[service]
        service_cache['forecasts'][location] = {
            'requested_at': datetime.now().strftime(TIMESTAMP_FMT),
            'data': data
        }
        self.cache[service] = service_cache

    def _get_icon(self, name):
        icon = 'icons/{}/{}.png'.format(self.config['icons'], name)
        if not os.path.exists(icon):
            if name.startswith('nt_'):
                # use the day icon
                icon = 'icons/{}/{}.png'.format(self.config['icons'], name[3:])
        if not os.path.exists(icon):
            # use the set default icon
            icon = 'icons/{}/{}.png'.format(self.config['icons'], 'default')
        if not os.path.exists(icon):
            # use the global unknown icon
            icon = '{}.png'.format('error')
        return icon

    def _get_today_word(self, sunrise, sunset):
        # the 'today' word is 'tonight' if it's less than 2 hours before sunset

        current_time = self._localize_time()
        try:
            if (sunset - current_time).seconds < 7200:
                return 'tonight'
            else:
                return 'today'
        except Exception:
            return 'today'

    def _get_current_date(self):
        '''Get the current date in the target location'''
        target_now = self._remotize_time(self._localize_time())
        return target_now.date()

    def _get_wund_weather(self):
        LOG.debug('getting weather from Weather Underground')
        location = '{},{}'.format(self.config['location']['latitude'],
                                  self.config['location']['longitude'])
        data = self._load_cached_data('wund', location)

        if data is None:
            wunderground.set_key(self.config['key.wund'])
            data = wunderground.forecast(location)
            self._save_cached_data('wund', location, data)

        def parse_alert(alert):
            data = { 'description': alert['description'] }
            try:
                data['expires'] = datetime.fromtimestamp(int(alert['expires_epoch']))
            except ValueError:
                data['expires'] = None
                LOG.warn('invalid expiration time: %s', alert['expires_epoch'])

            if 'level_meteoalarm' not in alert:
                # only generate URIs for US alerts
                try:
                    zone = alert['ZONES'][0]
                    data['uri'] = '{}/US/{}/{}.html'.format(
                        SERVICES['wund']['url'], zone['state'], zone['ZONE'])
                except:
                    location = '{},{}'.format(self.config['location']['latitude'],
                                              self.config['location']['longitude'])
                    data['uri'] = wunderground.get_forecast_url(location)
            return data

        weather = {'current': {}, 'forecast': [], 'info': {}}

        if 'alerts' in data:
            weather['alerts'] = [parse_alert(a) for a in data['alerts']]

        conditions = data['current_observation']
        weather['info']['time'] = datetime.strptime(
            self.cache['wund']['forecasts'][location]['requested_at'], TIMESTAMP_FMT)

        if 'moon_phase' in data:
            def to_time(time_dict):
                hour = int(time_dict['hour'])
                minute = int(time_dict['hour'])
                dt = datetime.now().replace(hour=hour, minute=minute)
                return self._localize_time(dt)

            moon_phase = data['moon_phase']
            weather['info']['sunrise'] = to_time(moon_phase['sunrise'])
            weather['info']['sunset'] = to_time(moon_phase['sunset'])

        try:
            r = urlparse.urlparse(conditions['icon_url'])
            parts = os.path.split(r[2])[-1]
            name, ext = os.path.splitext(parts)
            icon = name
        except:
            icon = conditions['icon']

        feelslike = self.config.get('feelslike', False)
        weather['feelslike'] = feelslike

        weather['current'] = {
            'weather': conditions['weather'],
            'icon': icon,
            'humidity': int(conditions['relative_humidity'][:-1])
        }

        temp_kind = 'feelslike' if feelslike else 'temp'

        if self.config['units'] == 'us':
            weather['current']['temp'] = float(conditions[temp_kind + '_f'])
        else:
            weather['current']['temp'] = float(conditions[temp_kind + '_c'])

        days = data['forecast']['simpleforecast']['forecastday']

        def get_day_info(day):
            d = day['date']
            fdate = date(day=d['day'], month=d['month'], year=d['year'])

            info = {
                'conditions': day['conditions'],
                'precip': day['pop'],
                'icon': day['icon'],
                'date': fdate
            }

            if self.config['units'] == 'us':
                info['temp_hi'] = day['high']['fahrenheit']
                info['temp_lo'] = day['low']['fahrenheit']
            else:
                info['temp_hi'] = day['high']['celsius']
                info['temp_lo'] = day['low']['celsius']

            return info

        forecast = [get_day_info(d) for d in days]
        weather['forecast'] = sorted(forecast, key=lambda d: d['date'])
        return weather

    def _get_fio_weather(self):
        LOG.debug('getting weather from Forecast.io')
        location = '{},{}'.format(self.config['location']['latitude'],
                                  self.config['location']['longitude'])
        data = self._load_cached_data('fio', location)

        if data is None or data['flags']['units'] != self.config['units']:
            forecastio.set_key(self.config['key.fio'])
            units = self.config['units']
            data = forecastio.forecast(location, params={'units': units})
            self._save_cached_data('fio', location, data)

        weather = {'current': {}, 'forecast': [], 'info': {}}

        if 'alerts' in data:
            alerts = []
            for alert in data['alerts']:
                alerts.append({
                    'description': alert['title'],
                    'expires': datetime.fromtimestamp(alert['expires']),
                    'uri': alert['uri']
                })
            weather['alerts'] = alerts

        conditions = data['currently']
        weather['info']['time'] = datetime.strptime(
            self.cache['fio']['forecasts'][location]['requested_at'], TIMESTAMP_FMT)

        feelslike = self.config.get('feelslike', False)
        weather['feelslike'] = feelslike
        temp_kind = 'apparentTemperature' if feelslike else 'temperature'

        weather['current'] = {
            'weather': conditions['summary'],
            'icon': FIO_TO_WUND.get(conditions['icon'], conditions['icon']),
            'humidity': conditions['humidity'] * 100,
            'temp':  float(conditions[temp_kind])
        }

        days = data['daily']['data']

        if len(days) > 0:
            today = days[0]
            weather['info']['sunrise'] = self._localize_time(datetime.fromtimestamp(
                int(today['sunriseTime'])))
            weather['info']['sunset'] = self._localize_time(datetime.fromtimestamp(
                int(today['sunsetTime'])))

        def get_day_info(day):
            fdate = self._remotize_time(datetime.fromtimestamp(day['time'])).date()
            if day['summary'][-1] == '.':
                day['summary'] = day['summary'][:-1]
            info = {
                'date': fdate,
                'conditions': day['summary'],
                'icon': FIO_TO_WUND.get(day['icon'], day['icon']),
                'temp_hi': int(round(day['temperatureMax'])),
                'temp_lo': int(round(day['temperatureMin'])),
            }
            if 'precipProbability' in day:
                info['precip'] = 100 * day['precipProbability']

            return info

        forecast = [get_day_info(d) for d in days]
        weather['forecast'] = sorted(forecast, key=lambda d: d['date'])
        return weather

    # commands ---------------------------------------------------------

    def tell_commands(self, query):
        query = query.strip()

        items = [
            Item('units', autocomplete='units ',
                 subtitle=u'Choose your preferred unit system'),
            Item('location', autocomplete='location ',
                 subtitle='Set your default location with a ZIP code or city name'),
            Item('icons', autocomplete='icons ',
                 subtitle='Choose an icon set'),
            Item('service', autocomplete='service ',
                 subtitle='Select your preferred weather provider'),
            Item('days', autocomplete='days ',
                 subtitle='Set the number of forecast days to show'),
            Item('feelslike', autocomplete='feelslike',
                 subtitle='Toggle whether to show "feels like" temperatures'),
            Item('format', autocomplete='format ',
                 subtitle='Select a time format or specify your own'),
            Item('about', autocomplete='about',
                 subtitle='Show system information'),
            Item('config', autocomplete='config',
                 subtitle='Open the config file'),
            Item('log', autocomplete='log',
                 subtitle='Open the debug log'),
        ]

        names = [i.title for i in items]

        if len(query) > 0:
            for name in names:
                if query.startswith(name):
                    query = query[len(name):]
                    return getattr(self, 'tell_' + name)(query)

            items = self.partial_match_list(query, items,
                key=lambda t: t.title)

        return items

    def do_command(self, query):
        cmd, sep, arg = query.partition('|')

        if cmd == 'open':
            from subprocess import call
            call(['open', arg])
        elif hasattr(self, 'do_' + cmd):
            getattr(self, 'do_' + cmd)(arg)
        else:
            LOG.error('Invalid command "%s"', cmd)

    # time format ------------------------------------------------------

    def tell_format(self, fmt):
        items = []
        now = datetime.now()

        if fmt:
            try:
                items.append(Item(now.strftime(fmt), arg='format|' + fmt, valid=True))
            except:
                items.append(Item('Waiting for input...'))
            items.append(Item('Python time format syntax...',
                                     arg='http://docs.python.org/2/library/'
                                         'datetime.html#strftime-and-strptime-'
                                         'behavior',
                                     valid=True))
        else:
            for fmt in TIME_FORMATS:
                items.append(Item(now.strftime(fmt), arg='format|' + fmt, valid=True))

        return items

    def do_format(self, fmt):
        if fmt.startswith('http://'):
            import webbrowser
            webbrowser.open(fmt)
        else:
            self.config['time_format'] = fmt
            now = datetime.now()
            self.puts('Showing times as {}'.format(now.strftime(fmt)))

    # icons ------------------------------------------------------------

    def tell_icons(self, ignored):
        items = []
        sets = [f for f in os.listdir('icons') if not f.startswith('.')]
        for iset in sets:
            uid = 'icons-{}'.format(iset)
            icon = 'icons/{}/{}.png'.format(iset, EXAMPLE_ICON)
            title = iset.capitalize()
            item = Item(title, uid=uid, icon=icon, arg=u'icons|' + iset, valid=True)

            info_file = os.path.join('icons', iset, 'info.json')
            if os.path.exists(info_file):
                with open(info_file, 'rt') as ifile:
                    info = json.load(ifile)
                    if 'description' in info:
                        item.subtitle = info['description']

            items.append(item)
        return items

    def do_icons(self, arg):
        self.config['icons'] = arg
        self.puts('Using {} icons'.format(arg))

    # days -------------------------------------------------------------

    def tell_days(self, days):
        if len(days) == 0:
            length = '{} day'.format(self.config['days'])
            if self.config['days'] != 1:
                length += 's'
            return [Item('Enter the number of forecast days to show...',
                         subtitle='Currently showing {} of forecast'.format(
                         length))]
        else:
            days = int(days)

            if days < 0 or days > 10:
                raise Exception('Value must be between 1 and 10')

            length = '{} day'.format(days)
            if days != 1:
                length += 's'
            return [Item('Show {} of forecast'.format(length), arg='days|{0}'.format(days),
                                valid=True)]

    def do_days(self, days):
        days = int(days)
        if days < 0 or days > 10:
            raise Exception('Value must be between 1 and 10')
        self.config['days'] = days

        length = '{} day'.format(days)
        if days != 1:
            length += 's'
        self.puts('Now showing {} of forecast'.format(length))

    # service ----------------------------------------------------------

    def tell_service(self, query):
        items = []

        for svc in SERVICES.keys():
            items.append(Item(SERVICES[svc]['name'], uid=svc, arg='service|' +svc,
                                     valid=True))

        if len(query.strip()) > 0:
            q = query.strip().lower()
            items = [i for i in items if q in i.title.lower()]

        return items

    def do_service(self, svc):
        self.config['service'] = svc

        key_name = 'key.{}'.format(svc)
        key = self.config.get(key_name)
        answer = alfred.get_from_user(
            'Update API key', u'Enter your API key for {}'.format(
            SERVICES[svc]['name']), value=key, extra_buttons='Get key')

        button, sep, key = answer.partition('|')
        if button == 'Ok':
            self.config[key_name] = key
            self.puts(u'Using {} for weather data with key {}'.format(
                 SERVICES[svc]['name'], key))
        elif button == 'Get key':
            import webbrowser
            webbrowser.open(SERVICES[svc]['getkey'])

    # units ------------------------------------------------------------

    def tell_units(self, arg):
        arg = arg.strip()

        items = [
            Item('US', u'US units (°F, in, mph)', arg='units|us',
                autocomplete='units US', valid=True),
            Item('SI', u'SI units (°C, cm, kph)', arg='units|si',
                autocomplete='units SI', valid=True)
        ]

        items = self.partial_match_list(arg, items,
            key=lambda t: t.title)

        if len(items) == 0:
            items.append(Item('Invalid units'))

        return items

    def do_units(self, units):
        self.config['units'] = units
        self.puts('Using {} units'.format(units.upper()))

    # location ---------------------------------------------------------

    def tell_location(self, query):
        items = []
        query = query.strip()

        if len(query) > 0:
            results = wunderground.autocomplete(query)
            for result in [r for r in results if r['type'] == 'city']:
                items.append(Item(result['name'], arg='location|' + result['name'],
                                  valid=True))
        else:
            items.append(Item('Enter a location...'))

        return items

    def do_location(self, name):
        location_data = glocation.geocode(name)

        short_name = name
        if re.match('\d+ - .*', name):
            short_name = name.partition(' - ')[2]
        if ',' in short_name:
            short_name = short_name.split(',')[0]

        tz = glocation.timezone(location_data['latitude'],
                                location_data['longitude'])

        location = {
            'name': name,
            'short_name': short_name,
            'latitude': location_data['latitude'],
            'longitude': location_data['longitude'],
            'timezone': tz['timeZoneId']
        }

        self.config['location'] = location
        self.puts(u'Using location {}'.format(name))

    # weather ----------------------------------------------------------

    def tell_weather(self, location):
        '''Tell the current conditions and forecast for a location'''
        self._validate_settings()

        location = location.strip()

        if len(location) > 0:
            self._update_location(location)

        if self.config['service'] == 'wund':
            weather = self._get_wund_weather()
        else:
            weather = self._get_fio_weather()

        items = []

        # alerts
        if 'alerts' in weather:
            for alert in weather['alerts']:
                item = Item(alert['description'], icon='error.png')
                if alert['expires']:
                    item.subtitle = 'Expires at {}'.format(alert['expires'].strftime(
                        self.config['time_format']))
                if 'uri' in alert:
                    item.arg = clean_str(alert['uri'])
                    item.valid = True
                items.append(item)

        # conditions
        tu = 'F' if self.config['units'] == 'us' else 'C'
        title = u'Currently in {}: {}'.format(
            self.config['location']['short_name'],
            weather['current']['weather'].capitalize())
        subtitle = u'{}°{},  {}% humidity,  local time is {}'.format(
            int(round(weather['current']['temp'])), tu,
            int(round(weather['current']['humidity'])),
            self._remotize_time().strftime(self.config['time_format']))
        if weather['feelslike']:
            subtitle = u'Feels like ' + subtitle

        icon = self._get_icon(weather['current']['icon'])
        arg = SERVICES[self.config['service']]['lib'].get_forecast_url(location)
        items.append(Item(title, subtitle, icon=icon, valid=True,
                     arg=clean_str(arg)))

        location = '{},{}'.format(self.config['location']['latitude'],
                                  self.config['location']['longitude'])

        # forecast
        days = weather['forecast']
        if len(days) > self.config['days']:
            days = days[:self.config['days']]

        today = self._get_current_date()
        offset = date.today() - today
        sunrise = weather['info']['sunrise']
        sunset = weather['info']['sunset']

        for day in days:
            if day['date'] == today:
                day_desc = self._get_today_word(sunrise, sunset).capitalize()
            elif day['date'].day - today.day == 1:
                day_desc = 'Tomorrow'
            else:
                day_desc = (day['date'] + offset).strftime('%A')

            title = u'{}: {}'.format(day_desc, day['conditions'].capitalize())
            subtitle = u'High: {}°{},  Low: {}°{}'.format(
                day['temp_hi'], tu, day['temp_lo'], tu)
            if 'precip' in day:
                subtitle += u',  Precip: {}%'.format(day['precip'])
            arg = SERVICES[self.config['service']]['lib'].get_forecast_url(
                location, day['date'])
            icon = self._get_icon(day['icon'])
            items.append(Item(title, subtitle, icon=icon, arg=clean_str(arg),
                                     valid=True))

        arg = SERVICES[self.config['service']]['url']
        time = weather['info']['time'].strftime(self.config['time_format'])

        items.append(Item(LINE, u'Fetched from {} at {}'.format(
                                 SERVICES[self.config['service']]['name'], time),
                                 icon='', arg=arg, valid=True))
        return items

    # feelslike --------------------------------------------------------

    def tell_feelslike(self, query):
        feelslike = self.config.get('feelslike', False)
        return [Item('Toggle whether to show "feels like" temperatures',
                     subtitle='Current value is ' + str(feelslike).lower(),
                     arg='feelslike', valid=True)]

    def do_feelslike(self, query):
        feelslike = self.config.get('feelslike', False)
        self.config['feelslike'] = not feelslike
        if self.config['feelslike']:
            self.puts('Showing "feels like" temperatures')
        else:
            self.puts('Showing actual temperatures')

    # about ------------------------------------------------------------

    def tell_about(self, name, query=''):
        import re
        import sys

        items = []

        readme = alfred.preferences['readme']
        version = re.match(r'.*\bVersion: (?P<ver>\d[.\d]+)\b.*', readme)
        version = version.group('ver')
        version_info = 'Version {}'.format(version)
        items.append(Item(version_info))

        py_ver = 'Python {:08X}'.format(sys.hexversion)
        items.append(Item(py_ver))

        return items

    # log --------------------------------------------------------------

    def tell_log(self, query):
        return [Item('Open the debug log', arg='open|' + self.log_file, valid=True)]

    # config -----------------------------------------------------------

    def tell_config(self, query):
        return [Item('Open the config file', arg='open|' + self.config_file, valid=True)]


if __name__ == '__main__':
    from sys import argv
    ap = WeatherWorkflow()
    getattr(ap, argv[1])(*argv[2:])
