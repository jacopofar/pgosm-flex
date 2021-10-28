#!/usr/bin/env python3
"""Python script to run PgOSM Flex.

Designed to be ran in Docker image:
    https://hub.docker.com/r/rustprooflabs/pgosm-flex
"""
import configparser
import datetime
import logging
import os
from pathlib import Path
import shutil
import sys
import subprocess
import time

import click

import osm2pgsql_recommendation as rec
import db


BASE_PATH_DEFAULT = '/app'
"""Default path for pgosm-flex project for Docker.
"""

DEFAULT_SRID = '3857'


def get_today():
    """Returns yyyy-mm-dd formatted string for today.

    Retunrs
    -------------------------
    today : str
    """
    today = datetime.datetime.today().strftime('%Y-%m-%d')
    return today

@click.command()
@click.option('--layerset', required=True,
              default='default',
              show_default='default',
              help=f'Layer set from PgOSM Flex to load.')
@click.option('--layerset-path', required=False,
              help=f'Custom path to load layerset INI from. Custom paths should be mounted to Docker via docker run -v ...')
@click.option('--ram', required=True,
              prompt='Server RAM (GB)',
              default=4,
              show_default=4,
              help='Amount of RAM in GB available on the server running this process. Used to determine appropriate osm2pgsql command via osm2pgsql-tuner.com API.')
@click.option('--region', required=False,
              show_default="north-america/us",
              default="north-america/us",
              help='Region name matching the filename for data sourced from Geofabrik. e.g. north-america/us')
@click.option('--subregion', required=False,
              default="district-of-columbia",
              show_default="district-of-columbia",
              help='Sub-region name matching the filename for data sourced from Geofabrik. e.g. district-of-columbia')
@click.option('--srid', required=False, default=DEFAULT_SRID,
              envvar="PGOSM_SRID",
              help="SRID for data in PostGIS.  Defaults to 3857")
@click.option('--pgosm-date', required=False,
              default=get_today(),
              envvar="PGOSM_DATE",
              help="Date of the data in YYYY-MM-DD format. If today (default), automatically downloads when files not found locally. Set to historic date to load locally archived PBF/MD5 file, will fail if both files do not exist.")
@click.option('--language', default=None,
              envvar="PGOSM_LANGUAGE",
              help="Set default language in loaded OpenStreetMap data when available.  e.g. 'en' or 'kn'.")
@click.option('--schema-name', required=False,
              default='osm',
              help="Change the final schema name, defaults to 'osm'.")
@click.option('--skip-nested',
              default=False,
              envvar="PGOSM_SKIP_NESTED_POLYGON",
              is_flag=True,
              help=f'When set, skips calculating nested admin polygons. Can be time consuming on large regions.')
@click.option('--data-only',
              default=False,
              envvar="PGOSM_DATA_SCHEMA_ONLY",
              is_flag=True,
              help="When set, skips running Sqitch and importing QGIS Styles.")
@click.option('--skip-dump', default=False, is_flag=True,
              help='Skips the final pg_dump at the end. Useful for local testing when not loading into more permanent instance.')
@click.option('--debug', is_flag=True,
              help='Enables additional log output')
@click.option('--basepath',
              required=False,
              default=BASE_PATH_DEFAULT,
              help='Debugging option. Used when testing locally and not within Docker')
@click.option('--input-file',
              required=False,
              default=None,
              help='Path of the input PBF file')
@click.option('--conn-str',
              required=False,
              default=None,
              help='External database connection string. If missing use incorporated one')

def run_pgosm_flex(layerset, layerset_path, ram, region, subregion, srid,
                    pgosm_date, language, schema_name, skip_nested, data_only,
                    skip_dump, debug, basepath, input_file, conn_str):
    """Logic to run PgOSM Flex within Docker.
    """
    if region is None and input_file is None:
        raise ValueError("either region or input_file must be provided")
    paths = get_paths(base_path=basepath)
    if input_file is not None:
        log_file = os.path.join(
            paths['out_path'],
            os.path.basename(input_file) + '.log',
        )
    else:
        # Required for optional user prompt
        if subregion == 'none':
            subregion = None

        log_file = get_log_path(region, subregion, paths)

    setup_logger(log_file, debug)
    logger = logging.getLogger('pgosm-flex')
    logger.info('PgOSM Flex starting...')

    set_env_vars(region, subregion, srid, language, pgosm_date,
                layerset, layerset_path, conn_str=conn_str)
    if input_file is None:
        prepare_data(region=region,
                    subregion=subregion,
                    pgosm_date=pgosm_date,
                    paths=paths)
        osm2pgsql_command = get_osm2pgsql_command(region=region,
                                                subregion=subregion,
                                                ram=ram,
                                                paths=paths)
    else:
        osm2pgsql_command = rec.osm2pgsql_recommendation(ram=ram,
                                           pbf_filename=input_file,
                                           out_path=paths['out_path'],
                                           conn_str=conn_str)

    wait_for_postgres(conn_str=conn_str)

    db.prepare_pgosm_db(data_only=data_only, paths=paths, conn_str=conn_str)

    run_osm2pgsql(osm2pgsql_command=osm2pgsql_command, paths=paths)

    if not skip_nested:
        # Auto-set skip_nested when place layer not imported
        skip_nested = check_layerset_places(layerset_path, layerset, paths)

    run_post_processing(paths=paths,
                        skip_nested=skip_nested, conn_str=conn_str)

    if input_file is None:
        remove_latest_files(region, subregion, paths)

        export_filename = get_export_filename(region,
                                            subregion,
                                            layerset,
                                            pgosm_date)
    else:
        export_filename = os.path.splitext(input_file)[0] + '.sql'

    if schema_name != 'osm':
        db.rename_schema(schema_name)

    if skip_dump:
        logger.info('Skipping pg_dump')
    else:
        db.run_pg_dump(export_filename,
                       out_path=paths['out_path'],
                       data_only=data_only,
                       schema_name=schema_name,
                       conn_str=conn_str
                       )
    logger.info('PgOSM Flex complete!')



def set_env_vars(region, subregion, srid, language, pgosm_date, layerset,
                 layerset_path, conn_str=None):
    """Sets environment variables needed by PgOSM Flex

    Parameters
    ------------------------
    region : str
    subregion : str
    srid : str
    language : str
    pgosm_date : str
    layerset : str
    layerset_path : str
        str when set, or None
    conn_str : str, optional
    """
    logger = logging.getLogger('pgosm-flex')
    logger.info('PgOSM Flex starting...')

    if subregion is None:
        pgosm_region = f'{region}'
    else:
        pgosm_region = f'{region}-{subregion}'

    logger.info(f'PGOSM_REGION: {pgosm_region}')
    os.environ['PGOSM_REGION'] = pgosm_region

    if srid != DEFAULT_SRID:
        logger.info(f'SRID set: {srid}')
        os.environ['PGOSM_SRID'] = str(srid)
    if language is not None:
        logger.info(f'Language set: {language}')
        os.environ['PGOSM_LANGUAGE'] = str(language)

    if layerset_path is not None:
        logger.info(f'Custom layerset path set: {layerset_path}')
        os.environ['PGOSM_LAYERSET_PATH'] = str(layerset_path)

    os.environ['PGOSM_DATE'] = pgosm_date

    os.environ['PGOSM_LAYERSET'] = layerset

    os.environ['PGOSM_CONN'] = (
        db.connection_string(db_name='pgosm')
        if conn_str is None
        else conn_str
    )


def setup_logger(log_file, debug):
    """Prepares logging.

    Parameters
    ------------------------------
    log_file : str
        Path to log file

    debug : bool
        Enables debug mode when True.  INFO when False.
    """
    if debug:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO

    log_format = '%(asctime)s:%(levelname)s:%(name)s:%(module)s:%(message)s'
    logging.basicConfig(filename=log_file,
                        level=log_level,
                        filemode='w',
                        format=log_format)

    # Reduce verbosity of urllib3 logging
    logging.getLogger('urllib3').setLevel(logging.INFO)

    logger = logging.getLogger('pgosm-flex')
    logger.setLevel(log_level)
    handler = logging.FileHandler(filename=log_file)
    handler.setLevel(log_level)
    formatter = logging.Formatter(log_format)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.debug('Logger configured')


def get_log_path(region, subregion, paths):
    """Returns path to log_file for given region/subregion.

    Parameters
    ---------------------
    region : str
    subregion : str
    paths : dict

    Returns
    ---------------------
    log_file : str
    """
    region_clean = region.replace('/', '-')
    if subregion is None:
        filename = f'{region_clean}.log'
    else:
        filename = f'{region_clean}-{subregion}.log'

    # Users will see this when they run, can copy/paste tail command.
    # Path matches path if following project's main README.md
    print(f'Log filename: {filename}')
    print('If running in Docker following procedures the file can be monitored')
    print(f'  tail -f ~/pgosm-data/{filename}')

    log_file = os.path.join(paths['out_path'], filename)
    return log_file


def get_paths(base_path):
    """Returns dictionary of various paths used.

    Creates `out_path` used for logs and data if necessary.

    Parameters
    -------------------
    base_path : str

    Returns
    -------------------
    paths : dict
    """
    db_path = os.path.join(base_path, 'db')
    out_path = os.path.join(base_path, 'output')
    flex_path = os.path.join(base_path, 'flex-config')
    paths = {'base_path': base_path,
             'db_path': db_path,
             'out_path': out_path,
             'flex_path': flex_path}

    Path(out_path).mkdir(parents=True, exist_ok=True)
    return paths

def get_region_filename(region, subregion):
    """Returns the filename needed to download/manage PBF files.

    Parameters
    ----------------------
    region : str
    subregion : str

    Returns
    ----------------------
    filename : str
    """
    base_name = '{}-latest.osm.pbf'
    if subregion is None:
        filename = base_name.format(region)
    else:
        filename = base_name.format(subregion)

    return filename


def get_export_filename(region, subregion, layerset, pgosm_date):
    """Returns the .sql filename to use from pg_dump.

    Parameters
    ----------------------
    region : str
    subregion : str
    layerset : str
    pgosm_date : str

    Returns
    ----------------------
    filename : str
    """
    region = region.replace('/', '-')
    subregion = subregion.replace('/', '-')
    if subregion is None:
        filename = f'pgosm-flex-{region}-{layerset}-{pgosm_date}.sql'
    else:
        filename = f'pgosm-flex-{region}-{subregion}-{layerset}-{pgosm_date}.sql'

    return filename


def get_pbf_url(region, subregion):
    """Returns the URL to the PBF for the region / subregion.

    Parameters
    ----------------------
    region : str
    subregion : str

    Returns
    ----------------------
    pbf_url : str
    """
    base_url = 'https://download.geofabrik.de'

    if subregion is None:
        pbf_url = f'{base_url}/{region}-latest.osm.pbf'
    else:
        pbf_url = f'{base_url}/{region}/{subregion}-latest.osm.pbf'

    return pbf_url


def wait_for_postgres(conn_str=None):
    """Ensures Postgres service is reliably ready for use.

    Required b/c Postgres process in Docker gets restarted shortly
    after starting.
    """
    logger = logging.getLogger('pgosm-flex')
    logger.info('Checking for Postgres service to be available')

    required_checks = 2
    found = 0
    i = 0
    max_loops = 30

    while found < required_checks:
        if i > max_loops:
            err = 'Postgres still has not started. Exiting.'
            logger.error(err)
            sys.exit(err)

        time.sleep(5)

        if db.pg_isready(conn_str=conn_str):
            found += 1
            logger.info(f'Postgres up {found} times')

        if i % 5 == 0:
            logger.info('Waiting...')

        if i > 100:
            err = 'Postgres still not available. Exiting.'
            logger.error(err)
            sys.exit(err)
        i += 1

    logger.info('Database passed two checks - should be ready')


def prepare_data(region, subregion, pgosm_date, paths):
    """Ensures the PBF file is available.

    Checks if it already exists locally, download if needed,
    and verify MD5 checksum.

    Parameters
    ----------------------
    region : str
    subregion : str
    pgosm_date : str
    paths : dict

    Returns
    ----------------------
    pbf_file : str
        Full path to PBF file
    """
    out_path = paths['out_path']
    pbf_filename = get_region_filename(region, subregion)

    pbf_file = os.path.join(out_path, pbf_filename)
    pbf_file_with_date = pbf_file.replace('latest', pgosm_date)

    md5_file = f'{pbf_file}.md5'
    md5_file_with_date = f'{pbf_file_with_date}.md5'

    if pbf_download_needed(pbf_file_with_date, md5_file_with_date, pgosm_date):
        logging.getLogger('pgosm-flex').info('Downloading PBF and MD5 files...')
        download_data(region, subregion, pbf_file, md5_file)
        archive_data(pbf_file, md5_file, pbf_file_with_date, md5_file_with_date)
    else:
        logging.getLogger('pgosm-flex').info('Copying Archived files')
        unarchive_data(pbf_file, md5_file, pbf_file_with_date, md5_file_with_date)

    verify_checksum(md5_file, paths)

    return pbf_file


def pbf_download_needed(pbf_file_with_date, md5_file_with_date, pgosm_date):
    """Decides if the PBF/MD5 files need to be downloaded.

    Parameters
    -------------------------------
    pbf_file_with_date : str
    md5_file_with_date : str

    Returns
    --------------------------
    download_needed : bool
    """
    logger = logging.getLogger('pgosm-flex')
    # If the PBF file exists, check for the MD5 file too.
    if os.path.exists(pbf_file_with_date):
        logger.info(f'PBF File exists {pbf_file_with_date}')

        if os.path.exists(md5_file_with_date):
            logger.info('PBF & MD5 files exist.  Download not needed')
            download_needed = False
        else:
            if pgosm_date == get_today():
                print('PBF for today available but not MD5... download needed')
                download_needed = True
            else:
                err = f'Missing MD5 file for {pgosm_date}. Cannot validate.'
                logger.error(err)
                raise FileNotFoundError(err)
    else:
        if not pgosm_date == get_today():
            err = f'Missing PBF file for {pgosm_date}. Cannot proceed.'
            logger.error(err)
            raise FileNotFoundError(err)

        logger.info('PBF file not found locally. Download required')
        download_needed = True

    return download_needed


def download_data(region, subregion, pbf_file, md5_file):
    """Downloads PBF and MD5 file using wget.

    Parameters
    ---------------------
    region : str
    subregion : str
    pbf_file : str
    md5_file : str
    """
    logger = logging.getLogger('pgosm-flex')
    logger.info(f'Downloading PBF data to {pbf_file}')
    pbf_url = get_pbf_url(region, subregion)

    subprocess.run(
        ['/usr/bin/wget', pbf_url,
         "-O", pbf_file , "--quiet"
        ],
        capture_output=True,
        text=True,
        check=True
    )

    logger.info(f'Downloading MD5 checksum to {md5_file}')
    subprocess.run(
        ['/usr/bin/wget', f'{pbf_url}.md5',
         "-O", md5_file , "--quiet"
        ],
        capture_output=True,
        text=True,
        check=True
    )


def verify_checksum(md5_file, paths):
    """If verfication fails, raises `CalledProcessError`

    Parameters
    ---------------------
    md5_file : str
    paths : dict
    """
    subprocess.run(['md5sum', '-c', md5_file],
                   capture_output=True,
                   text=True,
                   check=True,
                   cwd=paths['out_path'])



def archive_data(pbf_file, md5_file, pbf_file_with_date, md5_file_with_date):
    """Copies `pbf_file` and `md5_file` to `pbf_file_with_date` and
    `md5_file_with_date`.

    If either file exists, does nothing.

    Parameters
    --------------------------------
    pbf_file : str
    md5_file : str
    pbf_file_with_date : str
    md5_file_with_date : str
    """
    if os.path.exists(pbf_file_with_date):
        pass # Do nothing
    else:
        shutil.copy2(pbf_file, pbf_file_with_date)

    if os.path.exists(md5_file_with_date):
        pass # Do nothing
    else:
        shutil.copy2(md5_file, md5_file_with_date)


def unarchive_data(pbf_file, md5_file, pbf_file_with_date, md5_file_with_date):
    """Copies `pbf_file_with_date` and `md5_file_with_date`
    to `pbf_file` and `md5_file`.

    Always copies, will overwrite a -latest file if it is in the way.

    Parameters
    --------------------------------
    pbf_file : str
    md5_file : str
    pbf_file_with_date : str
    md5_file_with_date : str
    """
    logger = logging.getLogger('pgosm-flex')
    if os.path.exists(pbf_file):
        logger.debug(f'{pbf_file} exists. Overwriting.')

    logger.info(f'Copying {pbf_file_with_date} to {pbf_file}')
    shutil.copy2(pbf_file_with_date, pbf_file)

    if os.path.exists(md5_file):
        logger.debug(f'{md5_file} exists. Overwriting.')

    logger.info(f'Copying {md5_file_with_date} to {md5_file}')
    shutil.copy2(md5_file_with_date, md5_file)


def remove_latest_files(region, subregion, paths):
    """Removes the PBF and MD5 file with -latest in the name.

    Files are archived via prepare_data() before processing starts

    Parameters
    -------------------------
    region : str
    subregion : str
    paths : dict
    """
    pbf_filename = get_region_filename(region, subregion)

    pbf_file = os.path.join(paths['out_path'], pbf_filename)
    md5_file = f'{pbf_file}.md5'
    logging.info(f'Done with {pbf_file}, removing.')
    os.remove(pbf_file)
    logging.info(f'Done with {md5_file}, removing.')
    os.remove(md5_file)


def get_osm2pgsql_command(region, subregion, ram, paths, conn_str=None):
    """Returns recommended osm2pgsql command.

    Parameters
    ----------------------
    region : str
    subregion : str
    ram : int
    paths : dict
    conn_str : str, optional

    Returns
    ----------------------
    rec_cmd : str
        osm2pgsql command recommended by the API
    """
    pbf_filename = get_region_filename(region, subregion)
    rec_cmd = rec.osm2pgsql_recommendation(ram=ram,
                                           pbf_filename=pbf_filename,
                                           out_path=paths['out_path'],
                                           conn_str=conn_str)
    return rec_cmd


def run_osm2pgsql(osm2pgsql_command, paths):
    """Runs the provided osm2pgsql command.

    Parameters
    ----------------------
    osm2pgsql_command : str
    paths : dict
    """
    logger = logging.getLogger('pgosm-flex')
    logger.info(f'Running {osm2pgsql_command}')
    output = subprocess.check_output(osm2pgsql_command.split(),
                        text=True,
                        cwd=paths['flex_path'],
                        )
    # output from PgOSM Flex lua goes to stdout
    logger.info(f'PgOSM Flex output: \n {output}\nEND PgOSM Flex output')

def check_layerset_places(layerset_path, layerset, paths):
    """If `place` layer is not included `skip_nested` should be true.

    Parameters
    ------------------------
    layerset_path : str
    layerset : str
    paths : dict

    Returns
    ------------------------
    skip_nested : boolean
    """
    logger = logging.getLogger('pgosm-flex')

    if layerset_path is None:
        layerset_path = os.path.join(paths['flex_path'], 'layerset')
        logger.info(f'Using default layerset path {layerset_path}')

    ini_file = os.path.join(layerset_path, f'{layerset}.ini')
    config = configparser.ConfigParser()
    config.read(ini_file)
    try:
        place = config['layerset']['place']
    except KeyError:
        # No place key, skip_nested should be true
        logger.debug('Place layer not defined, setting skip_nested')
        return True

    # If Place is true
    if place:
        logger.debug('Place layer is defined as true. Not setting skip_nested')
        return False

    logger.debug('Place set to false, setting skip_nested')
    return True



def run_post_processing(paths, skip_nested, conn_str=None):
    """Runs steps following osm2pgsql import.

    Post-processing SQL scripts and (optionally) calculate nested admin polgyons

    Parameters
    ----------------------
    paths : dict

    skip_nested : bool
    conn_str : str, optional
    """

    db.pgosm_after_import(paths, conn_str=conn_str)
    logger = logging.getLogger('pgosm-flex')
    if skip_nested:
        logger.info('Skipping calculating nested polygons')
    else:
        logger.info('Calculating nested polygons')
        db.pgosm_nested_admin_polygons(paths, conn_str=conn_str)



if __name__ == "__main__":
    logging.getLogger('pgosm-flex').info('Running PgOSM Flex!')
    run_pgosm_flex()
