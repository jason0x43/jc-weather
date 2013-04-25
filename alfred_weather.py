#!/usr/bin/env python
# coding=UTF-8

from datetime import date, datetime, timedelta, tzinfo
from sys import stdout
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

show_exceptions = False

SETTINGS_VERSION = 3
SETTINGS_FILE = os.path.join(alfred.data_dir, 'settings.json')
CACHE_FILE = os.path.join(alfred.cache_dir, 'cache.json')
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
        self.stdoffset = timedelta(seconds = -time.timezone)
        if time.daylight:
            self.dstoffset = timedelta(seconds = -time.altzone)
        else:
            self.dstoffset = stdoffset
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


settings = {}
cache = {}
local_tz = LocalTimezone()


def _out(msg):
    '''Output a string'''
    stdout.write(msg.encode('utf-8'))


def _clean(arg):
    return arg.replace('&', '&amp;')


def _localize_time(dtime=None):
    '''
    Return a datetime from the configured location adjusted for the local
    timezone.

    If no time is specified, return a localized instance of the current time.
    '''
    if dtime:
        remote_tz = pytz.timezone(settings['location']['timezone'])
        remote_time = remote_tz.localize(dtime)
        return remote_time.astimezone(local_tz)
    else:
        return local_tz.localize(datetime.now())

def _remotize_time(dtime=None):
    '''
    Return a time from the local timezone location adjusted for the configured
    location.

    If no time is specified, return an instance of the current time in the
    remote location's timezone.
    '''
    remote_tz = pytz.timezone(settings['location']['timezone'])
    if dtime:
        local_time = local_tz.localize(dtime)
        return local_time.astimezone(remote_tz)
    else:
        now = local_tz.localize(datetime.now())
        return now.astimezone(remote_tz)


def _migrate_settings():
    if 'units' in settings:
        if settings['units'] == 'US':
            settings['units'] = 'us'
        else:
            settings['units'] = 'si'

    if 'key' in settings:
        settings['key.wund'] = settings['key']
        del settings['key']

    settings['service'] = 'wund'

    if 'name' in settings:
        location = glocation.geocode(settings['name'])
        name = location['name']
        short_name = name.partition(',')[0] if ',' in name else name
        settings['location'] = {
            'name': name,
            'short_name': short_name,
            'latitude': location['latitude'],
            'longitude': location['longitude']
        }
        del settings['name']

    if 'timezone' not in settings['location']:
        location = settings['location']
        tz = glocation.timezone(location['latitude'], location['longitude'])
        settings['location']['timezone'] = tz['timeZoneId']


def _load_settings(validate=True):
    '''Get an the location and units to use'''
    settings.clear()
    settings.update({
        'units': DEFAULT_UNITS,
        'icons': DEFAULT_ICONS,
        'time_format': DEFAULT_TIME_FMT,
        'days': 3,
        'version': SETTINGS_VERSION,
    })

    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'rt') as sf:
            settings.update(json.load(sf))
            if settings.get('version', 0) < SETTINGS_VERSION:
                _migrate_settings()
                _save_settings()

    if validate:
        if 'service' not in settings:
            raise SetupError('You need to set your weather service',
                             'Use the "wset service" command.')
        if 'location' not in settings:
            raise SetupError('Missing default location', 'You must specify a '
                             'default location with the "wset location" '
                             'command')

    return settings


def _save_settings():
    if not os.path.isdir(alfred.data_dir):
        os.mkdir(alfred.data_dir)
        if not os.access(alfred.data_dir, os.W_OK):
            raise IOError('No write access to dir: %s' % alfred.data_dir)
    with open(SETTINGS_FILE, 'wt') as sf:
        json.dump(settings, sf, indent=2)


def _load_cache():
    cache.clear()
    cache.update({'conditions': {}, 'forecasts': {}})
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'rt') as sf:
            cache.update(json.load(sf))


def _save_cache():
    if not os.path.isdir(alfred.cache_dir):
        os.mkdir(alfred.cache_dir)
        if not os.access(alfred.cache_dir, os.W_OK):
            raise IOError('No write access to dir: %s' % alfred.cache_dir)
    with open(CACHE_FILE, 'wt') as cf:
        json.dump(cache, cf, indent=2)


def _update_location(query):
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
    settings['location'].update(temp_loc)


def _load_cached_data(service, location):
    _load_cache()
    data = None
    if service not in cache:
        cache[service] = {'forecasts': {}}
    if location in cache[service]['forecasts']:
        last_check = cache[service]['forecasts'][location]['requested_at']
        last_check = datetime.strptime(last_check, TIMESTAMP_FMT)
        if (datetime.now() - last_check).seconds < 300:
            data = cache[service]['forecasts'][location]['data']
    return data


def _save_cached_data(service, location, data):
    _load_cache()
    if service not in cache:
        cache[service] = {'forecasts': {}}
    cache[service]['forecasts'][location] = {
        'requested_at': datetime.now().strftime(TIMESTAMP_FMT),
        'data': data
    }
    _save_cache()


def _get_icon(name):
    icon = 'icons/{}/{}.png'.format(settings['icons'], name)
    if not os.path.exists(icon):
        if name.startswith('nt_'):
            # use the day icon
            icon = 'icons/{}/{}.png'.format(settings['icons'], name[3:])
    if not os.path.exists(icon):
        # use the set default icon
        icon = 'icons/{}/{}.png'.format(settings['icons'], 'default')
    if not os.path.exists(icon):
        # use the global unknown icon
        icon = '{}.png'.format('error')
    return icon


def _get_today_word(sunrise, sunset):
    # the 'today' word is 'tonight' if it's less than 2 hours before sunset

    current_time = _localize_time()
    try:
        if (sunset - current_time).seconds < 7200:
            return 'tonight'
        else:
            return 'today'
    except Exception:
        return 'today'


def _get_current_date():
    '''Get the current date in the target location'''
    target_now = _remotize_time(_localize_time())
    return target_now.date()


def _get_wund_weather():
    location = '{},{}'.format(settings['location']['latitude'],
                              settings['location']['longitude'])
    data = _load_cached_data('wund', location)

    if data is None:
        wunderground.set_key(settings['key.wund'])
        data = wunderground.forecast(location)
        _save_cached_data('wund', location, data)

    def parse_alert(alert):
        data = {
            'description': alert['description'],
            'expires': datetime.fromtimestamp(int(alert['expires_epoch'])),
        }

        if 'level_meteoalarm' not in alert:
            # only generate URIs for US alerts
            zone = alert['ZONES'][0]
            data['uri'] = '{}/US/{}/{}.html'.format(SERVICES['wund']['url'],
                                                    zone['state'],
                                                    zone['ZONE'])
        return data

    weather = {'current': {}, 'forecast': [], 'info': {}}

    if 'alerts' in data:
        weather['alerts'] = [parse_alert(a) for a in data['alerts']]

    conditions = data['current_observation']
    weather['info']['time'] = datetime.strptime(
        cache['wund']['forecasts'][location]['requested_at'], TIMESTAMP_FMT)

    if 'moon_phase' in data:
        def to_time(time_dict):
            hour = int(time_dict['hour'])
            minute = int(time_dict['hour'])
            dt = datetime.now().replace(hour=hour, minute=minute)
            return _localize_time(dt)

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


    weather['current'] = {
        'weather': conditions['weather'],
        'icon': icon,
        'humidity': int(conditions['relative_humidity'][:-1])
    }
    if settings['units'] == 'us':
        weather['current']['temp'] = conditions['temp_f']
    else:
        weather['current']['temp'] = conditions['temp_c']

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

        if settings['units'] == 'us':
            info['temp_hi'] = day['high']['fahrenheit']
            info['temp_lo'] = day['low']['fahrenheit']
        else:
            info['temp_hi'] = day['high']['celsius']
            info['temp_lo'] = day['low']['celsius']

        return info

    forecast = [get_day_info(d) for d in days]
    weather['forecast'] = sorted(forecast, key=lambda d: d['date'])
    return weather


def _get_fio_weather():
    location = '{},{}'.format(settings['location']['latitude'],
                              settings['location']['longitude'])
    data = _load_cached_data('fio', location)

    if data is None or data['flags']['units'] != settings['units']:
        forecastio.set_key(settings['key.fio'])
        units = settings['units']
        data = forecastio.forecast(location, params={'units': units})
        _save_cached_data('fio', location, data)

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
        cache['fio']['forecasts'][location]['requested_at'], TIMESTAMP_FMT)

    weather['current'] = {
        'weather': conditions['summary'],
        'icon': FIO_TO_WUND.get(conditions['icon'], conditions['icon']),
        'humidity': conditions['humidity'] * 100,
        'temp':  conditions['temperature']
    }

    days = data['daily']['data']
    sunrise = None
    sunset = None

    if len(days) > 0:
        today = days[0]
        weather['info']['sunrise'] = _localize_time(datetime.fromtimestamp(
            int(today['sunriseTime'])))
        weather['info']['sunset'] = _localize_time(datetime.fromtimestamp(
            int(today['sunsetTime'])))

    def get_day_info(day):
        fdate = _remotize_time(datetime.fromtimestamp(day['time'])).date()
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


def tell_time_format(fmt):
    items = []
    now = datetime.now()

    if fmt:
        try:
            items.append(alfred.Item(now.strftime(fmt), arg=fmt, valid=True))
        except:
            items.append(alfred.Item('Waiting for input...'))
        items.append(alfred.Item('Python time format syntax...',
                                 arg='http://docs.python.org/2/library/'
                                     'datetime.html#strftime-and-strptime-'
                                     'behavior',
                                 valid=True))
    else:
        for fmt in TIME_FORMATS:
            items.append(alfred.Item(now.strftime(fmt), arg=fmt, valid=True))

    return items


def do_time_format(fmt):
    if fmt.startswith('http://'):
        import webbrowser
        webbrowser.open(fmt)
    else:
        _load_settings(False)
        settings['time_format'] = fmt
        _save_settings()

        now = datetime.now()
        _out('Showing times as {}'.format(now.strftime(fmt)))


def tell_icons(ignored):
    items = []
    sets = os.listdir('icons')
    for iset in sets:
        uid = 'icons-{}'.format(iset)
        icon = 'icons/{}/{}.png'.format(iset, EXAMPLE_ICON)
        title = iset.capitalize()
        item = alfred.Item(title, uid=uid, icon=icon, arg=iset, valid=True)

        info_file = os.path.join('icons', iset, 'info.json')
        if os.path.exists(info_file):
            with open(info_file, 'rt') as ifile:
                info = json.load(ifile)
                if 'description' in info:
                    item.subtitle = info['description']

        items.append(item)
    return items


def do_icons(arg):
    _load_settings(False)
    settings['icons'] = arg
    _save_settings()
    _out('Using {} icons'.format(arg))


def tell_key(query):
    items = []

    for svc in SERVICES.keys():
        items.append(alfred.Item(SERVICES[svc]['name'], uid=svc,
                                 arg=SERVICES[svc]['getkey'], valid=True))

    if len(query.strip()) > 0:
        q = query.strip().lower()
        items = [i for i in items if q in i.title.lower()]

    return items


def tell_days(days):
    if len(days.strip()) == 0:
        _load_settings(False)
        length = '{} day'.format(settings['days'])
        if settings['days'] != 1:
            length += 's'
        return [alfred.Item('Currently showing {} of forecast'.format(
                            length), 'Enter a new value to change')]
    else:
        days = int(days)

        if days < 0 or days > 10:
            raise Exception('Value must be between 1 and 10')

        length = '{} day'.format(days)
        if days != 1:
            length += 's'
        return [alfred.Item('Show {} of forecast'.format(length), arg=days,
                            valid=True)]


def do_days(days):
    days = int(days)
    if days < 0 or days > 10:
        raise Exception('Value must be between 1 and 10')
    _load_settings(False)
    settings['days'] = days
    _save_settings()

    length = '{} day'.format(days)
    if days != 1:
        length += 's'
    _out('Now showing {} of forecast'.format(length))


def tell_service(query):
    items = []

    for svc in SERVICES.keys():
        items.append(alfred.Item(SERVICES[svc]['name'], uid=svc, arg=svc,
                                 valid=True))

    if len(query.strip()) > 0:
        q = query.strip().lower()
        items = [i for i in items if q in i.title.lower()]

    return items


def do_service(svc):
    _load_settings(False)
    settings['service'] = svc

    key_name = 'key.{}'.format(svc)
    key = settings.get(key_name)
    answer = alfred.get_from_user(
        'Update API key', u'Enter your API key for {}'.format(
        SERVICES[svc]['name']), value=key, extra_buttons='Get key')

    button, sep, key = answer.partition('|')
    if button == 'Ok':
        settings[key_name] = key
        _save_settings()
        _out(u'Using {} for weather data with key {}'.format(
             SERVICES[svc]['name'], key))
    elif button == 'Get key':
        import webbrowser
        webbrowser.open(SERVICES[svc]['getkey'])


def tell_units(arg):
    items = []

    us = alfred.Item('US', u'US units (°F, in, mph)', arg='us', valid=True)
    metric = alfred.Item('SI', u'SI units (°C, cm, kph)', arg='si', valid=True)

    if len(arg.strip()) == 0:
        items.append(us)
        items.append(metric)
    elif 'us'.startswith(arg.lower()):
        items.append(us)
    elif 'metric'.startswith(arg.lower()):
        items.append(metric)
    else:
        items.append(alfred.Item('Invalid units'))

    return items


def do_units(units):
    _load_settings(False)
    settings['units'] = units
    _save_settings()
    _out('Using {} units'.format(units))


def tell_location(query):
    items = []

    if len(query.strip()) > 0:
        results = wunderground.autocomplete(query)
        for result in [r for r in results if r['type'] == 'city']:
            items.append(alfred.Item(result['name'], arg=result['name'],
                                     valid=True))

    return items


def do_location(name):
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

    _load_settings(False)
    settings['location'] = location
    _save_settings()
    _out(u'Using location {}'.format(name))


def tell_weather(location):
    '''Tell the current conditions and forecast for a location'''
    _load_settings()

    if len(location.strip()) > 0:
        _update_location(location)

    if settings['service'] == 'wund':
        weather = _get_wund_weather()
    else:
        weather = _get_fio_weather()

    items = []

    # alerts
    if 'alerts' in weather:
        for alert in weather['alerts']:
            subtitle = 'Expires at {}'.format(alert['expires'].strftime(
                       settings['time_format']))
            item = alfred.Item(alert['description'], subtitle=subtitle,
                               icon='error.png')
            if 'uri' in alert:
                item.arg = _clean(alert['uri'])
                item.valid = True
            items.append(item)

    # conditions
    tu = 'F' if settings['units'] == 'us' else 'C'
    title = u'Currently in {}: {}'.format(
        settings['location']['short_name'],
        weather['current']['weather'].capitalize())
    subtitle = u'{}°{},  {}% humidity,  local time is {}'.format(
        int(round(weather['current']['temp'])), tu,
        int(round(weather['current']['humidity'])),
        _remotize_time().strftime(settings['time_format']))

    icon = _get_icon(weather['current']['icon'])
    items.append(alfred.Item(title, subtitle, icon=icon))

    location = '{},{}'.format(settings['location']['latitude'],
                              settings['location']['longitude'])

    # forecast
    days = weather['forecast']
    if len(days) > settings['days']:
        days = days[:settings['days']]

    today = _get_current_date()
    offset = date.today() - today
    sunrise = weather['info']['sunrise']
    sunset = weather['info']['sunset']

    for day in days:
        if day['date'] == today:
            day_desc = _get_today_word(sunrise, sunset).capitalize()
        elif day['date'].day - today.day == 1:
            day_desc = 'Tomorrow'
        else:
            day_desc = (day['date'] + offset).strftime('%A')

        title = u'{}: {}'.format(day_desc, day['conditions'].capitalize())
        subtitle = u'High: {}°{},  Low: {}°{}'.format(
            day['temp_hi'], tu, day['temp_lo'], tu)
        if 'precip' in day:
            subtitle += u',  Precip: {}%'.format(day['precip'])
        arg = SERVICES[settings['service']]['lib'].get_forecast_url(
            location, day['date'])
        icon = _get_icon(day['icon'])
        items.append(alfred.Item(title, subtitle, icon=icon, arg=_clean(arg),
                                 valid=True))

    arg = SERVICES[settings['service']]['url']
    time = weather['info']['time'].strftime(settings['time_format'])

    items.append(alfred.Item(LINE, u'Fetched from {} at {}'.format(
                             SERVICES[settings['service']]['name'], time),
                             icon='', arg=arg, valid=True))
    return items


def tell(name, query=''):
    '''Tell something'''
    try:
        cmd = 'tell_{}'.format(name)
        if cmd in globals():
            items = globals()[cmd](query)
        else:
            items = [alfred.Item('Invalid action "{}"'.format(name))]
    except SetupError, e:
        if show_exceptions:
            import traceback
            traceback.print_exc()
        items = [alfred.Item(e.title, e.subtitle, icon='error.png')]
    except Exception, e:
        if show_exceptions:
            import traceback
            traceback.print_exc()
        items = [alfred.Item(str(e), icon='error.png')]

    _out(alfred.to_xml(items))


def do(name, query=''):
    '''Do something'''
    try:
        cmd = 'do_{}'.format(name)
        if cmd in globals():
            globals()[cmd](query)
        else:
            _out('Invalid command "{}"'.format(name))
    except Exception, e:
        if show_exceptions:
            import traceback
            traceback.print_exc()
        _out('Error: {}'.format(e))


if __name__ == '__main__':
    show_exceptions = True
    from sys import argv
    globals()[argv[1]](*argv[2:])
