#!/usr/bin/env python

'''
Use the Google location APIs to lookup information about physical locations.
'''

import requests
import time

def geocode(location):
    '''Get the physical coordiantes of a place (ZIP, city, address, etc).'''
    api = 'http://maps.googleapis.com/maps/api/geocode/json'
    params = {'address': location, 'sensor': 'false'}
    r = requests.get(api, params=params).json()

    if r.get('status') == 'OK':
        results = r['results'][0]
        data = {
            'name': results['formatted_address'],
            'latitude': results['geometry']['location']['lat'],
            'longitude': results['geometry']['location']['lng']
        }
        return data

    raise Exception('Request failed')


def timezone(lat, lng):
    '''Get the timezone of a physical location.'''
    api = 'https://maps.googleapis.com/maps/api/timezone/json'
    params = {
        'location': '{},{}'.format(lat, lng),
        'timestamp': int(time.time()),
        'sensor': 'false'
    }
    r = requests.get(api, params=params).json()

    if r.get('status') == 'OK':
        return r

    raise Exception('Request failed')


if __name__ == '__main__':
    from argparse import ArgumentParser
    parser = ArgumentParser()
    parser.add_argument('operation', choices=('geo', 'tz'))
    parser.add_argument('args', nargs='+')
    args = parser.parse_args()

    from pprint import pformat
    if args.operation == 'tz':
        print pformat(timezone(*args.args))
    elif args.operation == 'geo':
        print pformat(geocode(*args.args))
