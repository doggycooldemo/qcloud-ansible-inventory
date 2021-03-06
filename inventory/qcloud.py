#!/usr/bin/env python

import argparse
import ConfigParser
import os
import subprocess
import re
import errno

from time import time
from collections import defaultdict

try:
    import json
except ImportError:
    import simplejson as json


class QcloudClient:
    BATCH_SIZE = 100

    def describe(self, resource):
        page = 1
        received = 0
        total = 0

        while page == 1 or received < total:
            command = ['tccli', resource, 'DescribeInstances', ]
            resp = json.loads(subprocess.check_output(command))
            page += 1
            received += self.BATCH_SIZE
            total = resp['TotalCount']

            for item in resp['InstanceSet']:
                yield item


class QcloudInventory:
    def _empty_index(self):
        index = defaultdict(list, {'_meta': {'hostvars': {}}})
        return index

    def __init__(self):
        self.client = QcloudClient()
        self.read_settings()
        self.parse_cli_args()
        self.load_inventory()

        if self.args.host:
            host_vars = self.inventory['index']['_meta']['hostvars'][self.args.host]
            data_to_print = host_vars or {}
        else:
            data_to_print = self.inventory['index']

        print self.json_format_dict(data_to_print, True)

    def read_settings(self):
        """ Reads the settings from the qcloud.ini file """

        config = self.config = ConfigParser.RawConfigParser()
        script_file = os.path.realpath(__file__)
        config_dir = os.path.dirname(script_file)
        config_basename = os.path.basename(script_file).rsplit('.', 1)[0] + '.ini'
        config.read('/'.join([config_dir, config_basename]))

        # Cache related
        self.cache_path = config.get('cache', 'path')
        cache_dir = os.path.dirname(self.cache_path)
        try:
            os.makedirs(cache_dir)
        except OSError as exc:
            if exc.errno == errno.EEXIST and os.path.isdir(cache_dir):
                pass
            else: raise

        # Determine whether max_age parameters exist
        try:
            config.getint('cache', 'max_age')
        except:
            self.cache_max_age = 86400
        else:
            self.cache_max_age = config.getint('cache', 'max_age')

        # Determine whether cache_disable parameters exist
        try:
            config.getboolean('cache', 'cache_disable')
        except:
            self.cache_disable = False
        else:
            self.cache_disable = config.getboolean('cache', 'cache_disable')

    def is_cache_valid(self):
        """ Determines if the cache files have expired, or if it is still valid """

        if os.path.isfile(self.cache_path):
            mod_time = os.path.getmtime(self.cache_path)
            current_time = time()
            return (mod_time + self.cache_max_age) > current_time
        else:
            return False

    def build_inventory(self):
        index = self._empty_index()
        self.add_cvm(index)

        return {'index': index}

    def add_cvm(self, index):
        for cvm in self.client.describe('cvm'):
            # safe_name = self.to_safe(cvm['InstanceId'])
            safe_name = self.to_safe(cvm['InstanceName'])
            index['cvm'].append(safe_name)
            cvm = self.extract_ips(cvm)
            ssh_options = self.ssh_options('cvm', safe_name, cvm)
            index['_meta']['hostvars'][safe_name] = dict(ssh_options, qcloud=cvm)

    def extract_ips(self, instance):
        ips = dict()
        for key, value in instance.iteritems():
            if isinstance(value, dict) and 'IpAddress' in value and isinstance(value['IpAddress'], list) and len(
                    value['IpAddress']) > 0 and key.endswith('IpAddress'):
                ips[key[:-len('Address')]] = value['IpAddress'][0]

        instance.update(ips)

        eips = dict()
        for key, value in instance.iteritems():
            if isinstance(value, dict) and 'IpAddress' in value and len(value['IpAddress']) > 0 and key.endswith(
                    'EipAddress'):
                eips['EipAddress'] = value['IpAddress']

        instance.update(eips)

        vips = dict()
        for key, value in instance.iteritems():
            if isinstance(value, dict) and 'PrivateIpAddress' in value and len(
                    value['PrivateIpAddress']['IpAddress']) > 0 and key.endswith('VpcAttributes'):
                vips['Vip'] = value['PrivateIpAddress']['IpAddress'][0]

        instance.update(vips)
        return instance

    def ssh_options(self, kind, name, instance):
        options = dict(self.config.items(kind))
        specific_section = '.'.join([kind, name])
        if self.config.has_section(specific_section):
            options.update(self.config.items(specific_section))

        return {
            'ansible_ssh_user': options['user'] % instance,
            'ansible_ssh_host': options['host'] % instance,
            'ansible_ssh_port': options['port'] % instance
        }

    def load_inventory(self):
        if self.args.refresh_cache or not self.is_cache_valid() or self.cache_disable:
            self.inventory = self.build_inventory()
            self.write_cache()
        else:
            self.read_cache()

    def write_cache(self):
        json_data = self.json_format_dict(self.inventory, True)
        cache = open(self.cache_path, 'w')
        cache.write(json_data)
        cache.close()

    def read_cache(self):
        cache = open(self.cache_path, 'r')
        json_data = cache.read()
        self.inventory = json.loads(json_data)

    def to_safe(self, word):
        ''' Converts 'bad' characters in a string to underscores so they can be
        used as Ansible groups '''

        return re.sub("[^A-Za-z0-9\-]", "_", word)

    def parse_cli_args(self):
        ''' Command line argument processing '''

        parser = argparse.ArgumentParser(description='Produce an Ansible Inventory file based on Aliyun')
        parser.add_argument('--list', action='store_true', default=True,
                            help='List instances (default: True)')
        parser.add_argument('--host', action='store',
                            help='Get all the variables about a specific instance')
        parser.add_argument('--refresh-cache', action='store_true', default=False,
                            help='Force refresh of cache by making API requests to Aliyun (default: False - use cache files)')
        self.args = parser.parse_args()

    def json_format_dict(self, data, pretty=False):
        if pretty:
            return json.dumps(data, sort_keys=True, indent=2)
        else:
            return json.dumps(data)


QcloudInventory()
