#!/usr/bin/env python
# coding=UTF-8

from datetime import date, datetime
from sys import stdout
import alfred
import forecastio
import geocode
import json
import os
import os.path
import re
import time
import urlparse
import wunderground

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

settings_file = os.path.join(alfred.data_dir, 'settings.json')
cache_file = os.path.join(alfred.cache_dir, 'cache.json')

show_exceptions = False

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


class SetupError(Exception):
    def __init__(self, title, subtitle):
        super(SetupError, self).__init__(title)
        self.title = title
        self.subtitle = subtitle


def _out(msg):
    '''Output a string'''
    stdout.write(msg.encode('utf-8'))


def _clean(arg):
    return arg.replace('&', '&amp;')


def _migrate_settings(settings):
    if 'units' in settings:
        if settings['units'] == 'US':
            settings['units'] = 'us'
        else:
            settings['units'] = 'si'
    if 'key' in settings:
        settings['key.wund'] = settings['key']
        del settings['key']
    settings['service'] = 'wund'
    settings['location'] = {}
    if 'name' in settings:
        location = geocode.lookup(settings['name'])
        name = location['name']
        short_name = name.partition(',')[0] if ',' in name else name
        settings['location']['name'] = name
        settings['location']['short_name'] = short_name
        settings['location']['latitude'] = location['latitude']
        settings['location']['longitude'] = location['longitude']
        del settings['name']


def _load_settings(validate=True):
    '''Get an the location and units to use'''
    settings = {
        'units': DEFAULT_UNITS,
        'icons': DEFAULT_ICONS,
        'time_format': DEFAULT_TIME_FMT,
        'days': 3,
        'version': 2
    }

    if os.path.exists(settings_file):
        with open(settings_file, 'rt') as sf:
            s = json.load(sf)
            if 'version' not in s:
                _migrate_settings(s)
            settings.update(s)

    if validate:
        if 'service' not in settings:
            raise SetupError('You need to set your weather service',
                             'Use the "wset service" command.')
        if 'location' not in settings:
            raise SetupError('Missing default location', 'You must specify a '
                             'default location with the "wset location" '
                             'command')

    return settings


def _save_settings(settings):
    if not os.path.isdir(alfred.data_dir):
        os.mkdir(alfred.data_dir)
        if not os.access(alfred.data_dir, os.W_OK):
            raise IOError('No write access to dir: %s' % alfred.data_dir)
    with open(settings_file, 'wt') as sf:
        json.dump(settings, sf, indent=2)


def _load_cache():
    cache = {'conditions': {}, 'forecasts': {}}
    if os.path.exists(cache_file):
        with open(cache_file, 'rt') as sf:
            cache = json.load(sf)
    return cache


def _save_cache(cache):
    if not os.path.isdir(alfred.cache_dir):
        os.mkdir(alfred.cache_dir)
        if not os.access(alfred.cache_dir, os.W_OK):
            raise IOError('No write access to dir: %s' % alfred.cache_dir)
    with open(cache_file, 'wt') as cf:
        json.dump(cache, cf, indent=2)


def _get_temp_location(query, settings):
    location = geocode.lookup(query)
    name = location['name']
    short_name = name.partition(',')[0] if ',' in name else name

    settings['location'] = {
        'name': name,
        'short_name': short_name,
        'latitude': location['latitude'],
        'longitude': location['longitude']
    }


def _load_cached_data(service, location):
    cache = _load_cache()
    data = None
    if service not in cache:
        cache[service] = {'forecasts': {}}
    if location in cache[service]['forecasts']:
        last_check = cache[service]['forecasts'][location]['requested_at']
        last_check = datetime.strptime(last_check, TIMESTAMP_FMT)
        if (datetime.now() - last_check).seconds < 300:
            data = cache[service]['forecasts'][location]['data']
    return data, cache


def _save_cached_data(service, location, data):
    cache = _load_cache()
    if service not in cache:
        cache[service] = {'forecasts': {}}
    cache[service]['forecasts'][location] = {
        'requested_at': datetime.now().strftime(TIMESTAMP_FMT),
        'data': data
    }
    _save_cache(cache)
    return cache


def _get_icon(settings, name):
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


def _get_wund_weather(settings, location):
    location = '{},{}'.format(settings['location']['latitude'],
                              settings['location']['longitude'])
    data, cache = _load_cached_data('wund', location)

    if data is None:
        wunderground.set_key(settings['key.wund'])
        data = wunderground.forecast(location)
        cache = _save_cached_data('wund', location, data)

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
    today = date.today()

    def get_day_info(day):
        d = day['date']
        fdate = date(day=d['day'], month=d['month'], year=d['year'])

        info = {
            'conditions': day['conditions'],
            'precip': day['pop'],
            'icon': day['icon'],
            'date': fdate.strftime('%Y-%m-%d')
        }

        if fdate == today:
            info['day'] = 'Today'
        elif fdate.day - today.day == 1:
            info['day'] = 'Tomorrow'
        else:
            info['day'] = fdate.strftime('%A')

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


def _get_fio_weather(settings, location):
    location = '{},{}'.format(settings['location']['latitude'],
                              settings['location']['longitude'])
    data, cache = _load_cached_data('fio', location)

    if data is None or data['flags']['units'] != settings['units']:
        forecastio.set_key(settings['key.fio'])
        units = settings['units']
        data = forecastio.forecast(location, params={'units': units})
        cache = _save_cached_data('fio', location, data)

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
    today = date.today()

    def get_day_info(day):
        fdate = date.fromtimestamp(day['time'])
        if day['summary'][-1] == '.':
            day['summary'] = day['summary'][:-1]
        info = {
            'date': fdate.strftime('%Y-%m-%d'),
            'conditions': day['summary'],
            'icon': FIO_TO_WUND.get(day['icon'], day['icon']),
            'temp_hi': int(round(day['temperatureMax'])),
            'temp_lo': int(round(day['temperatureMin'])),
        }
        if 'precipProbability' in day:
            info['precip'] = 100 * day['precipProbability']

        if fdate == today:
            info['day'] = 'Today'
        elif fdate.day - today.day == 1:
            info['day'] = 'Tomorrow'
        else:
            info['day'] = fdate.strftime('%A')

        return info

    forecast = [get_day_info(d) for d in days]
    weather['forecast'] = sorted(forecast, key=lambda d: d['date'])
    return weather


def tell_time_format(ignored):
    items = []
    for fmt in TIME_FORMATS:
        now = datetime.now()
        items.append(alfred.Item(now.strftime(fmt), arg=fmt, valid=True))
    return items


def do_time_format(fmt):
    settings = _load_settings(False)
    settings['time_format'] = fmt
    _save_settings(settings)

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
    settings = _load_settings(False)
    settings['icons'] = arg
    _save_settings(settings)
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
        settings = _load_settings(False)
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
    settings = _load_settings(False)
    settings['days'] = days
    _save_settings(settings)

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
    settings = _load_settings(False)
    settings['service'] = svc

    key_name = 'key.{}'.format(svc)
    key = settings.get(key_name)
    user_key = alfred.get_from_user(
        'Update API key', u'Enter your API key for {}'.format(
        SERVICES[svc]['name']), value=key)

    if len(user_key) != 0:
        key = user_key
        settings[key_name] = user_key

    _save_settings(settings)
    _out(u'Using {} for weather data with key {}'.format(SERVICES[svc]['name'],
         key))


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
    settings = _load_settings(False)
    settings['units'] = units
    _save_settings(settings)
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
    location_data = geocode.lookup(name)

    short_name = name
    if re.match('\d+ - .*', name):
        short_name = name.partition(' - ')[2]
    if ',' in short_name:
        short_name = short_name.split(',')[0]

    location = {
        'name': name,
        'short_name': short_name,
        'latitude': location_data['latitude'],
        'longitude': location_data['longitude']
    }

    settings = _load_settings(False)
    settings['location'] = location
    _save_settings(settings)
    _out(u'Using location {}'.format(name))


def tell_weather(location):
    '''Tell the current conditions and forecast for a location'''
    settings = _load_settings()

    if len(location.strip()) > 0:
        _get_temp_location(location, settings)

    if settings['service'] == 'wund':
        weather = _get_wund_weather(settings, location)
    else:
        weather = _get_fio_weather(settings, location)

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
    subtitle = u'{}°{},  {}% humidity'.format(
        int(round(weather['current']['temp'])), tu,
        int(round(weather['current']['humidity'])))

    icon = _get_icon(settings, weather['current']['icon'])
    items.append(alfred.Item(title, subtitle, icon=icon))

    location = '{},{}'.format(settings['location']['latitude'],
                              settings['location']['longitude'])

    # forecast
    days = weather['forecast']
    if len(days) > settings['days']:
        days = days[:settings['days']]
    for day in days:
        title = u'{}: {}'.format(day['day'], day['conditions'].capitalize())
        subtitle = u'High: {}°{},  Low: {}°{}'.format(
            day['temp_hi'], tu, day['temp_lo'], tu)
        if 'precip' in day:
            subtitle += u',  Precip: {}%'.format(day['precip'])
        arg = SERVICES[settings['service']]['lib'].get_forecast_url(
            location, day['date'])
        icon = _get_icon(settings, day['icon'])
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
