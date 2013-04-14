#!/usr/bin/env python

import requests

settings = {
    'api': 'http://api.wunderground.com/api/{}',
    'key': None
}


class WeatherException(Exception):
    def __init__(self, error):
        super(WeatherException, self).__init__(error['description'])
        self.error = error


def set_key(key):
    settings['key'] = key
    settings['api'] = settings['api'].format(key)


def conditions(location):
    '''
    Get current conditions for a location

    The location may be a US ZIP code or a 'state/city' path like
    'OH/Fairborn' or 'NY/New_York'.
    '''
    url = '{}/conditions/q/{}.json'.format(settings['api'], location)
    r = requests.get(url).json()

    if 'error' in r['response']:
        raise WeatherException(r['response']['error'])
    if 'current_observation' not in r:
        print r
        raise Exception('Invalid location')
    return r['current_observation']


def forecast(location):
    '''
    Get a 5-day forecast conditions for a location

    The location may be a US ZIP code or a 'state/city' path like
    'OH/Fairborn' or 'NY/New_York'.
    '''
    url = '{}/forecast/q/{}.json'.format(settings['api'], location)
    r = requests.get(url).json()
    if 'error' in r['response']:
        raise WeatherException(r['response']['error'])
    return r['forecast']


def autocomplete(query):
    '''Return autocomplete values for a query'''
    url = 'http://autocomplete.wunderground.com/aq?query={}'.format(query)
    return requests.get(url).json()['RESULTS']


if __name__ == '__main__':
    from argparse import ArgumentParser
    from pprint import pformat

    parser = ArgumentParser()
    parser.add_argument('function', choices=('conditions', 'forecast',
                                             'autocomplete'))
    parser.add_argument('location', help='ZIP code')
    parser.add_argument('-k', '--key', help='API key')
    args = parser.parse_args()

    if args.key:
        set_key(args.key)

    func = globals()[args.function]
    print pformat(func(args.location))
