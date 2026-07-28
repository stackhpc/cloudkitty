# Copyright 2026
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
import numbers
import time

from oslo_config import cfg
from oslo_log import log as logging

from cloudkitty import service
from cloudkitty import storage
from cloudkitty.utils import tz as tzutils

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


cli_opts = [
    cfg.StrOpt(
        'migrate_from',
        help='Name of the source storage backend.',
    ),
    cfg.StrOpt(
        'begin',
        help='Optional begin bound for the migration scope in ISO-8601.',
    ),
    cfg.StrOpt(
        'end',
        help='Optional end bound for the migration scope in ISO-8601.',
    ),
    cfg.IntOpt(
        'page_size',
        default=1000,
        min=1,
        help='Maximum number of measures to retrieve per page.',
    ),
    cfg.BoolOpt(
        'overwrite_destination',
        default=False,
        help='Delete destination data in the migration scope before copying.',
    ),
]

CONF.register_cli_opts(cli_opts)


class StorageMigrationError(Exception):
    """Raised when the storage migration cannot complete."""


def resolve_bounds(source_storage, begin=None, end=None):
    discovered = source_storage.get_bounds()
    return {
        'begin': begin if begin is not None else discovered['begin'],
        'end': end if end is not None else discovered['end'],
    }


def assert_destination_empty(destination_storage, begin, end):
    frames = destination_storage.retrieve(
        begin=begin,
        end=end,
        offset=0,
        limit=1,
    )
    if frames['total']:
        raise StorageMigrationError(
            'Destination backend already contains data in the migration '
            'scope.'
        )


def overwrite_destination(destination_storage, begin, end):
    destination_storage.delete(
        begin=begin,
        end=end,
    )


def copy_dataframes(source_storage, destination_storage, begin, end,
                    page_size=1000):
    copied = 0
    offset = 0

    while True:
        frames = source_storage.retrieve(
            begin=begin,
            end=end,
            offset=offset,
            limit=page_size,
        )
        dataframes = frames['dataframes']
        if not dataframes:
            break

        destination_storage.push(dataframes)

        copied_in_page = _count_measures(dataframes)
        copied += copied_in_page
        offset += copied_in_page

        if copied >= frames['total']:
            break

    return copied


def verify_migration(source_storage, destination_storage, begin, end,
                     max_attempts=1, retry_interval=1):
    for attempt in range(1, max_attempts + 1):
        try:
            source_frames = source_storage.retrieve(
                begin=begin,
                end=end,
                offset=0,
                limit=1,
            )
            destination_frames = destination_storage.retrieve(
                begin=begin,
                end=end,
                offset=0,
                limit=1,
            )

            if source_frames['total'] != destination_frames['total']:
                raise StorageMigrationError(
                    'Source and destination dataframe counts do not match.'
                )

            source_total = source_storage.total(
                begin=begin,
                end=end,
                paginate=False,
            )
            destination_total = destination_storage.total(
                begin=begin,
                end=end,
                paginate=False,
            )
            if (_normalize_total(source_total) !=
                    _normalize_total(destination_total)):
                raise StorageMigrationError(
                    'Source and destination overall totals do not match.'
                )

            source_grouped_total = source_storage.total(
                begin=begin,
                end=end,
                groupby=['type'],
                paginate=False,
            )
            destination_grouped_total = destination_storage.total(
                begin=begin,
                end=end,
                groupby=['type'],
                paginate=False,
            )
            if (_normalize_total(source_grouped_total) !=
                    _normalize_total(destination_grouped_total)):
                raise StorageMigrationError(
                    'Source and destination grouped totals do not match.'
                )
            return
        except StorageMigrationError as exc:
            if attempt == max_attempts:
                raise
            LOG.info(
                'Migration verification attempt %s/%s failed: %s Retrying '
                'after %s second(s).',
                attempt,
                max_attempts,
                exc,
                retry_interval,
            )
            time.sleep(retry_interval)


def _count_measures(dataframes):
    count = 0
    for frame in dataframes:
        if hasattr(frame, 'iterpoints'):
            count += len(list(frame.iterpoints()))
        else:
            count += 1
    return count


def _normalize_total(total):
    output = {'total': total['total']}
    results = []

    for result in total['results']:
        normalized = {}
        for key, value in result.items():
            if key == 'price':
                key = 'rate'
            if isinstance(value, numbers.Number):
                value = round(float(value), 5)
            normalized[key] = value
        results.append(normalized)

    output['results'] = sorted(
        results,
        key=lambda result: repr(sorted(result.items())),
    )
    return output


class StorageMigrationCommand(object):
    def __init__(self, migrate_from, begin=None, end=None, page_size=1000,
                 overwrite_destination=False):
        self.migrate_from = migrate_from
        self.begin = begin
        self.end = end
        self.page_size = page_size
        self.overwrite_destination = overwrite_destination

    def run(self):
        version = int(CONF.storage.version)
        if version != 2:
            raise StorageMigrationError(
                'Storage migration only supports v2 backends.'
            )
        if not self.migrate_from:
            raise StorageMigrationError('A source backend must be provided.')

        destination_backend = CONF.storage.backend
        if self.migrate_from == destination_backend:
            raise StorageMigrationError(
                'Source and destination backends must be different.'
            )

        try:
            source_storage = storage.get_storage(
                backend=self.migrate_from,
                version=version,
            )
        except Exception as exc:
            raise StorageMigrationError(
                'Unable to load source backend %s.' % self.migrate_from
            ) from exc

        try:
            destination_storage = storage.get_storage(version=version)
        except Exception as exc:
            raise StorageMigrationError(
                'Unable to load destination backend %s.'
                % destination_backend
            ) from exc

        bounds = resolve_bounds(
            source_storage,
            begin=self.begin,
            end=self.end,
        )
        if bounds['begin'] is None and bounds['end'] is None:
            LOG.info('Source backend %s is empty. Nothing to migrate.',
                     self.migrate_from)
            return

        if self.overwrite_destination:
            LOG.info(
                'Deleting destination data in scope [%s, %s) before '
                'migration.',
                bounds['begin'],
                bounds['end'],
            )
            overwrite_destination(
                destination_storage,
                bounds['begin'],
                bounds['end'],
            )
        else:
            assert_destination_empty(
                destination_storage,
                bounds['begin'],
                bounds['end'],
            )
        copied = copy_dataframes(
            source_storage,
            destination_storage,
            bounds['begin'],
            bounds['end'],
            page_size=self.page_size,
        )
        LOG.info(
            'Copied %s measures from %s to %s.',
            copied,
            self.migrate_from,
            destination_backend,
        )
        verify_migration(
            source_storage,
            destination_storage,
            bounds['begin'],
            bounds['end'],
            max_attempts=30,
        )


def main():
    service.prepare_service()
    begin = tzutils.dt_from_iso(CONF.begin) if CONF.begin else None
    end = tzutils.dt_from_iso(CONF.end) if CONF.end else None
    command = StorageMigrationCommand(
        CONF.migrate_from,
        begin=begin,
        end=end,
        page_size=CONF.page_size,
        overwrite_destination=CONF.overwrite_destination,
    )
    command.run()
