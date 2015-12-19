# -*- coding: utf-8 -*-
# ###
# Copyright (c) 2015, Rice University
# This software is subject to the provisions of the GNU Affero General
# Public License version 3 (AGPLv3).
# See LICENCE.txt for details.
# ###

try:
    import configparser
except ImportError:
    # python 2
    import ConfigParser as configparser
import difflib
import functools
import glob
import os
import psycopg2
import re
import sys
import subprocess


def get_settings_from_config(filename, config_names, settings):
    config = configparser.ConfigParser()
    config.read(filename)
    for name in config_names:
        setting_name = name.replace('-', '_')
        if settings.get(setting_name):
            # don't overwrite settings given from the CLI
            continue

        for section_name in config.sections():
            try:
                value = config.get(section_name, name)
                settings[setting_name] = value
                break
            except configparser.NoOptionError:
                pass


def with_cursor(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not kwargs.get('db_connection_string'):
            raise Exception('db-connection-string missing')
        db_conn_str = kwargs.get('db_connection_string')
        with psycopg2.connect(db_conn_str) as db_conn:
            with db_conn.cursor() as cursor:
                return func(cursor, *args, **kwargs)
    return wrapper


def import_migration(path):
    dirname, basename = os.path.split(path)
    package_name = dirname.replace('/', '.')
    module_name = basename.rsplit('.', 1)[0]
    __import__(package_name, fromlist=[module_name])
    module = sys.modules['{}.{}'.format(package_name, module_name)]
    return module


def get_migrations(migration_directory, import_modules=False, reverse=False):
    python_files = os.path.join(migration_directory, '*.py')
    for path in sorted(glob.glob(python_files), reverse=reverse):
        filename = os.path.basename(path)
        m = re.match('([0-9]+)_(.+).py$', filename)
        if m:
            version, migration_name = m.groups()
            if import_modules:
                yield version, migration_name, import_migration(path)
            else:
                yield version, migration_name


def get_schema_versions(cursor):
    cursor.execute('SELECT * FROM schema_migrations ORDER BY version')
    for i in cursor.fetchall():
        yield i[0]


def get_pending_migrations(migration_directory, cursor, import_modules=False,
                           up_to_version=None):
    migrated_versions = list(get_schema_versions(cursor))
    if up_to_version and up_to_version in migrated_versions:
        raise StopIteration
    migrations = list(get_migrations(migration_directory, import_modules))
    versions = [m[0] for m in migrations]
    if up_to_version:
        try:
            migrations = migrations[:versions.index(up_to_version) + 1]
        except ValueError:
            raise Exception('Version "{}" not found'.format(up_to_version))
    for migration in migrations:
        version = migration[0]
        if version not in migrated_versions:
            yield migration


def compare_schema(db_connection_string, callback, *args, **kwargs):
    old_schema = subprocess.check_output(
        ['pg_dump', '-s', db_connection_string]).decode('utf-8')
    callback(*args, **kwargs)
    new_schema = subprocess.check_output(
        ['pg_dump', '-s', db_connection_string]).decode('utf-8')
    print(''.join(list(
        difflib.unified_diff(old_schema.splitlines(True),
                             new_schema.splitlines(True),
                             n=10))))


def run_migration(cursor, version, migration_name, migration):
    print('Running migration {} {}'.format(version, migration_name))
    migration.up(cursor)
    cursor.execute('INSERT INTO schema_migrations VALUES (%s)', (version,))
    cursor.connection.commit()


def rollback_migration(cursor, version, migration_name, migration):
    print('Rolling back migration {} {}'.format(version, migration_name))
    migration.down(cursor)
    cursor.execute('DELETE FROM schema_migrations WHERE version = %s',
                   (version,))
    cursor.connection.commit()