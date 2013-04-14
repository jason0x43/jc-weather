#!/usr/bin/env python
# coding=UTF-8

from datetime import date, datetime
from sys import stdout
import alfred
import json
import os
import os.path
import re
import traceback
import weather


settings_file = os.path.join(alfred.data_dir, 'settings.json')
cache_file = os.path.join(alfred.cache_dir, 'cache.json')
default_units = 'US'
ts_format = '%Y-%m-%d %H:%M:%S'


class SetupError(Exception):
    def __init__(self, title, subtitle):
        super(SetupError, self).__init__(title)
        self.title = title
        self.subtitle = subtitle


def _out(msg):
    '''Output a string'''
    stdout.write(msg.encode('utf-8'))


def _load_settings(validate=True):
    '''Get an the location and units to use'''
    settings = {}
    if os.path.exists(settings_file):
        with open(settings_file, 'rt') as sf:
            settings = json.load(sf)

    if validate:
        if 'key' not in settings:
            raise SetupError('Missing API key', 'You need to set an API key '
                             'with the "wset key" command')
        if 'location' not in settings:
            raise SetupError('Missing default location', 'You must specify a '
                             'default location with the "wset location" '
                             'command')

    if 'units' not in settings:
        settings['units'] = default_units
    if 'icons' not in settings:
        settings['icons'] = 'grzanka'

    return settings


def _save_settings(settings):
    if not os.path.isdir(alfred.data_dir):
        os.mkdir(alfred.data_dir)
        if not os.access(alfred.data_dir, os.W_OK):
            raise IOError('No write access to dir: %s' % alfred.data_dir)
    with open(settings_file, 'wt') as sf:
        json.dump(settings, sf)


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
        json.dump(cache, cf)


def tell_icons(ignored):
    items = []
    sets = os.listdir('icons')
    for iset in sets:
        uid = 'icons-{}'.format(iset)
        icon = 'icons/{}/tstorms.png'.format(iset)
        title = iset.capitalize()
        item = alfred.Item(uid, title, icon=icon, arg=iset, valid=True)

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


def do_key(key):
    settings = _load_settings(False)
    settings['key'] = key.strip()
    _save_settings(settings)
    _out('Set API key to {}'.format(key))


def tell_units(arg):
    items = []

    us = alfred.Item('us', 'US', u'US units (°F, in, mph)', arg='US',
                     valid=True)
    metric = alfred.Item('metric', 'Metric', u'Metric units (°C, cm, kph)',
                         arg='metric', valid=True)

    if len(arg.strip()) == 0:
        items.append(us)
        items.append(metric)
    elif 'us'.startswith(arg.lower()):
        items.append(us)
    elif 'metric'.startswith(arg.lower()):
        items.append(metric)
    else:
        items.append(alfred.Item('bad', 'Invalid units'))

    return items


def do_units(units):
    settings = _load_settings(False)
    settings['units'] = units
    _save_settings(settings)
    _out('Using {} units'.format(units))


def tell_location(query):
    items = []

    if len(query.strip()) > 0:
        results = weather.autocomplete(query)
        for result in [r for r in results if r['type'] == 'city']:
            arg = '{}|zmw:{}'.format(result['name'], result['zmw'])
            items.append(alfred.Item(result['zmw'], result['name'], arg=arg,
                                     valid=True))

    return items


def do_location(location):
    if '|' in location:
        name, sep, location = location.partition('|')
    else:
        name = location

    if re.match('\d+ - .*', name):
        code, sep, name = name.partition(' - ')
    if ',' in name:
        name = name.split(',')[0]

    settings = _load_settings(False)
    settings['location'] = location
    settings['name'] = name
    _save_settings(settings)
    _out(u'Using location {}'.format(name))


def _get_temp_location(query, settings):
    new_loc = weather.autocomplete(query)[0]

    name = new_loc['name']
    if re.match('\d+ - .*', name):
        code, sep, name = name.partition(' - ')

    settings['name'] = name
    settings['location'] = 'zmw:{}'.format(new_loc['zmw'])


def tell_conditions(location):
    '''Tell the current conditions for a location'''
    settings = _load_settings()

    if len(location.strip()) > 0:
        try:
            _get_temp_location(location, settings)
        except Exception:
            raise Exception('Invalid location')

    location = settings['location']
    cache = _load_cache()
    conditions = None

    if location in cache['conditions']:
        last_check = cache['conditions'][location]['requested_at']
        last_check = datetime.strptime(last_check, ts_format)
        if (datetime.now() - last_check).seconds < 300:
            conditions = cache['conditions'][location]['data']

    if conditions is None:
        weather.set_key(settings['key'])
        conditions = weather.conditions(location)
        cache['conditions'][location] = {
            'requested_at': datetime.now().strftime(ts_format),
            'data': conditions
        }
        _save_cache(cache)

    if settings['units'] == 'US':
        temp = u'{}°F'.format(conditions['temp_f'])
    else:
        temp = u'{}°C'.format(conditions['temp_c'])

    items = []
    title = u'{}: {}'.format(settings['name'], conditions['weather'], temp)
    subtitle = u'{},  {} humidity'.format(temp,
                                          conditions['relative_humidity'])
    icon = 'icons/{}/{}.png'.format(settings['icons'], conditions['icon'])
    items.append(alfred.Item('conditions', title, subtitle, icon=icon))

    return items


def tell_forecast(location):
    '''Tell the forecast for a location'''
    settings = _load_settings()

    if len(location.strip()) > 0:
        try:
            _get_temp_location(location, settings)
        except Exception:
            traceback.print_exc()
            return [alfred.Item('bad-location', 'Invalid location')]

    forecast = None
    location = settings['location']
    cache = _load_cache()

    if location in cache['forecasts']:
        last_check = cache['forecasts'][location]['requested_at']
        last_check = datetime.strptime(last_check, ts_format)
        if (datetime.now() - last_check).seconds < 300:
            forecast = cache['forecasts'][location]['data']

    if forecast is None:
        weather.set_key(settings['key'])
        forecast = weather.forecast(settings['location'])
        cache['forecasts'][location] = {
            'requested_at': datetime.now().strftime(ts_format),
            'data': forecast
        }
        _save_cache(cache)

    days = forecast['simpleforecast']['forecastday']
    today = date.today()

    def create_day_item(day):
        d = day['date']
        fdate = date(day=d['day'], month=d['month'], year=d['year'])
        if fdate == today:
            day_name = 'Today'
        elif fdate.day - today.day == 1:
            day_name = 'Tomorrow'
        else:
            day_name = fdate.strftime('%A')

        uid = 'forecast-{}'.format(fdate.strftime('%Y-%m-%d'))
        title = '{}: {}'.format(day_name, day['conditions'])

        if settings['units'] == 'US':
            hi_temp = u'{}°F'.format(day['high']['fahrenheit'])
            lo_temp = u'{}°F'.format(day['low']['fahrenheit'])
        else:
            hi_temp = u'{}°C'.format(day['high']['celsius'])
            lo_temp = u'{}°C'.format(day['low']['celsius'])
        precip = 'Precipitation: {}%'.format(day['pop'])

        subtitle = u'High: {},  Low: {},  {}'.format(hi_temp, lo_temp, precip)
        icon = 'icons/{}/{}.png'.format(settings['icons'], day['icon'])

        return alfred.Item(uid, title, subtitle, icon=icon)

    items = [create_day_item(day) for day in days]
    return sorted(items, key=lambda item: item.uid)


def tell_weather(location):
    '''Tell the current conditions and forecast for a location'''
    conditions = tell_conditions(location)[0]
    forecast = tell_forecast(location)

    conditions.title = 'Currently in ' + conditions.title
    return [conditions] + forecast


def tell(name, query=''):
    '''Tell something'''
    try:
        cmd = 'tell_{}'.format(name)
        if cmd in globals():
            items = globals()[cmd](query)
        else:
            items = [alfred.Item('tell', 'Invalid action "{}"'.format(name))]
    except weather.WeatherException, e:
        items = [alfred.Item('no-weather', e.message, icon='error.png')]
    except SetupError, e:
        items = [alfred.Item(None, e.title, e.subtitle, icon='error.png')]
    except Exception, e:
        items = [alfred.Item(None, str(e), icon='error.png')]

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
        _out('Error: {}'.format(e))


if __name__ == '__main__':
    tell('weather')
