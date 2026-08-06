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
import datetime
from unittest import mock

from cloudkitty.cli import storage_migrate
from cloudkitty import tests


class ResolveBoundsTest(tests.TestCase):

    def test_resolve_bounds_uses_discovered_bounds_when_not_provided(self):
        source = mock.Mock()
        expected = {
            'begin': datetime.datetime(2024, 1, 1, 0, 0),
            'end': datetime.datetime(2024, 1, 2, 0, 0),
        }
        source.get_bounds.return_value = expected

        self.assertEqual(expected, storage_migrate.resolve_bounds(source))
        source.get_bounds.assert_called_once_with()

    def test_resolve_bounds_uses_provided_begin_and_discovered_end(self):
        source = mock.Mock()
        source.get_bounds.return_value = {
            'begin': datetime.datetime(2024, 1, 1, 0, 0),
            'end': datetime.datetime(2024, 1, 2, 0, 0),
        }
        begin = datetime.datetime(2024, 1, 1, 12, 0)

        self.assertEqual(
            {
                'begin': begin,
                'end': datetime.datetime(2024, 1, 2, 0, 0),
            },
            storage_migrate.resolve_bounds(source, begin=begin),
        )
        source.get_bounds.assert_called_once_with()

    def test_resolve_bounds_uses_discovered_begin_and_provided_end(self):
        source = mock.Mock()
        source.get_bounds.return_value = {
            'begin': datetime.datetime(2024, 1, 1, 0, 0),
            'end': datetime.datetime(2024, 1, 2, 0, 0),
        }
        end = datetime.datetime(2024, 1, 1, 18, 0)

        self.assertEqual(
            {
                'begin': datetime.datetime(2024, 1, 1, 0, 0),
                'end': end,
            },
            storage_migrate.resolve_bounds(source, end=end),
        )
        source.get_bounds.assert_called_once_with()

    def test_resolve_bounds_does_not_discover_when_both_provided(self):
        source = mock.Mock()
        begin = datetime.datetime(2024, 1, 1, 12, 0)
        end = datetime.datetime(2024, 1, 1, 18, 0)

        self.assertEqual(
            {
                'begin': begin,
                'end': end,
            },
            storage_migrate.resolve_bounds(source, begin=begin, end=end),
        )
        source.get_bounds.assert_not_called()

    def test_resolve_bounds_returns_empty_bounds_for_empty_source(self):
        source = mock.Mock()
        source.get_bounds.return_value = {
            'begin': None,
            'end': None,
        }

        self.assertEqual(
            {'begin': None, 'end': None},
            storage_migrate.resolve_bounds(source),
        )


class DestinationEmptyTest(tests.TestCase):

    def test_assert_destination_empty_allows_empty_destination(self):
        destination = mock.Mock()
        destination.retrieve.return_value = {
            'total': 0,
            'dataframes': [],
        }
        begin = datetime.datetime(2024, 1, 1, 0, 0)
        end = datetime.datetime(2024, 1, 2, 0, 0)

        storage_migrate.assert_destination_empty(destination, begin, end)

        destination.retrieve.assert_called_once_with(
            begin=begin,
            end=end,
            offset=0,
            limit=1,
        )

    def test_assert_destination_empty_fails_for_non_empty_destination(self):
        destination = mock.Mock()
        destination.retrieve.return_value = {
            'total': 1,
            'dataframes': [mock.sentinel.frame],
        }

        self.assertRaises(
            storage_migrate.StorageMigrationError,
            storage_migrate.assert_destination_empty,
            destination,
            datetime.datetime(2024, 1, 1, 0, 0),
            datetime.datetime(2024, 1, 2, 0, 0),
        )


class CopyDataFramesTest(tests.TestCase):

    def test_copy_dataframes_copies_single_page(self):
        source = mock.Mock()
        destination = mock.Mock()
        frame = mock.sentinel.frame
        source.retrieve.return_value = {
            'total': 1,
            'dataframes': [frame],
        }

        copied = storage_migrate.copy_dataframes(
            source,
            destination,
            datetime.datetime(2024, 1, 1, 0, 0),
            datetime.datetime(2024, 1, 2, 0, 0),
            page_size=10,
        )

        self.assertEqual(1, copied)
        destination.push.assert_called_once_with([frame])
        source.retrieve.assert_called_once_with(
            begin=datetime.datetime(2024, 1, 1, 0, 0),
            end=datetime.datetime(2024, 1, 2, 0, 0),
            offset=0,
            limit=10,
        )

    def test_copy_dataframes_copies_multiple_pages(self):
        source = mock.Mock()
        destination = mock.Mock()
        frames = [
            mock.sentinel.frame1,
            mock.sentinel.frame2,
            mock.sentinel.frame3,
        ]
        source.retrieve.side_effect = [
            {'total': 3, 'dataframes': frames[:2]},
            {'total': 3, 'dataframes': frames[2:]},
        ]

        copied = storage_migrate.copy_dataframes(
            source,
            destination,
            datetime.datetime(2024, 1, 1, 0, 0),
            datetime.datetime(2024, 1, 2, 0, 0),
            page_size=2,
        )

        self.assertEqual(3, copied)
        destination.push.assert_has_calls([
            mock.call(frames[:2]),
            mock.call(frames[2:]),
        ])

    def test_copy_dataframes_stops_on_source_retrieve_failure(self):
        source = mock.Mock()
        destination = mock.Mock()
        source.retrieve.side_effect = RuntimeError('retrieve failed')

        self.assertRaises(
            RuntimeError,
            storage_migrate.copy_dataframes,
            source,
            destination,
            datetime.datetime(2024, 1, 1, 0, 0),
            datetime.datetime(2024, 1, 2, 0, 0),
        )

        destination.push.assert_not_called()

    def test_copy_dataframes_stops_on_destination_push_failure(self):
        source = mock.Mock()
        destination = mock.Mock()
        source.retrieve.return_value = {
            'total': 1,
            'dataframes': [mock.sentinel.frame],
        }
        destination.push.side_effect = RuntimeError('push failed')

        self.assertRaises(
            RuntimeError,
            storage_migrate.copy_dataframes,
            source,
            destination,
            datetime.datetime(2024, 1, 1, 0, 0),
            datetime.datetime(2024, 1, 2, 0, 0),
        )


class VerifyMigrationTest(tests.TestCase):

    def _build_storage(self, frame_total=1, total_results=None,
                       grouped_results=None):
        storage_backend = mock.Mock()
        storage_backend.retrieve.return_value = {
            'total': frame_total,
            'dataframes': [],
        }
        storage_backend.total.side_effect = [
            total_results or {
                'total': 1,
                'results': [{'qty': 1.0, 'rate': 2.0}],
            },
            grouped_results or {
                'total': 1,
                'results': [{'type': 'instance', 'qty': 1.0, 'rate': 2.0}],
            },
        ]
        return storage_backend

    def test_verify_migration_succeeds_when_counts_and_totals_match(self):
        source = self._build_storage()
        destination = self._build_storage()

        storage_migrate.verify_migration(
            source,
            destination,
            datetime.datetime(2024, 1, 1, 0, 0),
            datetime.datetime(2024, 1, 2, 0, 0),
        )

    @mock.patch.object(storage_migrate.time, 'sleep')
    def test_verify_migration_retries_transient_count_mismatch(self,
                                                               sleep_mock):
        source = self._build_storage()
        destination = self._build_storage()
        destination.retrieve.side_effect = [
            {'total': 0, 'dataframes': []},
            {'total': 1, 'dataframes': []},
        ]

        storage_migrate.verify_migration(
            source,
            destination,
            datetime.datetime(2024, 1, 1, 0, 0),
            datetime.datetime(2024, 1, 2, 0, 0),
            max_attempts=2,
            retry_interval=3,
        )

        sleep_mock.assert_called_once_with(3)

    def test_verify_migration_fails_when_frame_counts_differ(self):
        source = self._build_storage(frame_total=2)
        destination = self._build_storage(frame_total=1)

        self.assertRaises(
            storage_migrate.StorageMigrationError,
            storage_migrate.verify_migration,
            source,
            destination,
            datetime.datetime(2024, 1, 1, 0, 0),
            datetime.datetime(2024, 1, 2, 0, 0),
        )

    def test_verify_migration_fails_when_overall_totals_differ(self):
        source = self._build_storage()
        destination = self._build_storage(
            total_results={
                'total': 1,
                'results': [{'qty': 1.0, 'rate': 3.0}],
            },
        )

        self.assertRaises(
            storage_migrate.StorageMigrationError,
            storage_migrate.verify_migration,
            source,
            destination,
            datetime.datetime(2024, 1, 1, 0, 0),
            datetime.datetime(2024, 1, 2, 0, 0),
        )

    def test_verify_migration_fails_when_grouped_totals_differ(self):
        source = self._build_storage()
        destination = self._build_storage(
            grouped_results={
                'total': 1,
                'results': [{'type': 'volume', 'qty': 1.0, 'rate': 2.0}],
            },
        )

        self.assertRaises(
            storage_migrate.StorageMigrationError,
            storage_migrate.verify_migration,
            source,
            destination,
            datetime.datetime(2024, 1, 1, 0, 0),
            datetime.datetime(2024, 1, 2, 0, 0),
        )


class StorageMigrationCommandTest(tests.TestCase):

    def setUp(self):
        super(StorageMigrationCommandTest, self).setUp()

    def test_run_fails_when_storage_version_is_not_v2(self):
        self.conf.set_override('version', 1, 'storage')

        with mock.patch.object(storage_migrate.storage, 'get_storage') as get:
            self.assertRaises(
                storage_migrate.StorageMigrationError,
                storage_migrate.StorageMigrationCommand('influxdb').run,
            )

        get.assert_not_called()

    def test_run_fails_when_source_matches_destination(self):
        self.conf.set_override('version', 2, 'storage')
        self.conf.set_override('backend', 'influxdb', 'storage')

        with mock.patch.object(storage_migrate.storage, 'get_storage') as get:
            self.assertRaises(
                storage_migrate.StorageMigrationError,
                storage_migrate.StorageMigrationCommand('influxdb').run,
            )

        get.assert_not_called()

    def test_run_fails_when_source_backend_cannot_be_loaded(self):
        self.conf.set_override('version', 2, 'storage')
        self.conf.set_override('backend', 'opensearch', 'storage')

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=RuntimeError('unknown backend')):
            self.assertRaises(
                storage_migrate.StorageMigrationError,
                storage_migrate.StorageMigrationCommand('missing').run,
            )

    def test_run_returns_without_copy_when_source_is_empty(self):
        self.conf.set_override('version', 2, 'storage')
        self.conf.set_override('backend', 'opensearch', 'storage')

        source = mock.Mock()
        destination = mock.Mock()

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=[source, destination]), \
                mock.patch.object(
                    storage_migrate,
                    'resolve_bounds',
                    return_value={'begin': None, 'end': None}), \
                mock.patch.object(
                    storage_migrate,
                    'assert_destination_empty') as empty_mock, \
                mock.patch.object(
                    storage_migrate,
                    'copy_dataframes') as copy_mock, \
                mock.patch.object(
                    storage_migrate,
                    'verify_migration') as verify_mock:
            storage_migrate.StorageMigrationCommand('influxdb').run()

        empty_mock.assert_not_called()
        copy_mock.assert_not_called()
        verify_mock.assert_not_called()

    def test_run_returns_without_copy_for_empty_source_with_begin(self):
        self.conf.set_override('version', 2, 'storage')
        self.conf.set_override('backend', 'opensearch', 'storage')

        source = mock.Mock()
        source.get_bounds.return_value = {'begin': None, 'end': None}
        destination = mock.Mock()
        begin = datetime.datetime(2024, 1, 1, 0, 0)

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=[source, destination]), \
                mock.patch.object(
                    storage_migrate,
                    'assert_destination_empty') as empty_mock, \
                mock.patch.object(
                    storage_migrate,
                    'copy_dataframes') as copy_mock, \
                mock.patch.object(
                    storage_migrate,
                    'verify_migration') as verify_mock:
            storage_migrate.StorageMigrationCommand(
                'influxdb',
                begin=begin,
            ).run()

        source.get_bounds.assert_called_once_with()
        empty_mock.assert_not_called()
        copy_mock.assert_not_called()
        verify_mock.assert_not_called()

    def test_run_returns_without_copy_for_empty_source_with_end(self):
        self.conf.set_override('version', 2, 'storage')
        self.conf.set_override('backend', 'opensearch', 'storage')

        source = mock.Mock()
        source.get_bounds.return_value = {'begin': None, 'end': None}
        destination = mock.Mock()
        end = datetime.datetime(2024, 1, 2, 0, 0)

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=[source, destination]), \
                mock.patch.object(
                    storage_migrate,
                    'assert_destination_empty') as empty_mock, \
                mock.patch.object(
                    storage_migrate,
                    'copy_dataframes') as copy_mock, \
                mock.patch.object(
                    storage_migrate,
                    'verify_migration') as verify_mock:
            storage_migrate.StorageMigrationCommand(
                'influxdb',
                end=end,
            ).run()

        source.get_bounds.assert_called_once_with()
        empty_mock.assert_not_called()
        copy_mock.assert_not_called()
        verify_mock.assert_not_called()

    def test_run_does_not_copy_when_destination_is_not_empty(self):
        self.conf.set_override('version', 2, 'storage')
        self.conf.set_override('backend', 'opensearch', 'storage')

        source = mock.Mock()
        destination = mock.Mock()
        bounds = {
            'begin': datetime.datetime(2024, 1, 1, 0, 0),
            'end': datetime.datetime(2024, 1, 2, 0, 0),
        }

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=[source, destination]), \
                mock.patch.object(
                    storage_migrate,
                    'resolve_bounds',
                    return_value=bounds), \
                mock.patch.object(
                    storage_migrate,
                    'assert_destination_empty',
                    side_effect=storage_migrate.StorageMigrationError), \
                mock.patch.object(
                    storage_migrate,
                    'copy_dataframes') as copy_mock, \
                mock.patch.object(
                    storage_migrate,
                    'verify_migration') as verify_mock:
            self.assertRaises(
                storage_migrate.StorageMigrationError,
                storage_migrate.StorageMigrationCommand('influxdb').run,
            )

        copy_mock.assert_not_called()
        verify_mock.assert_not_called()

    def test_run_overwrites_destination_when_requested(self):
        self.conf.set_override('version', 2, 'storage')
        self.conf.set_override('backend', 'opensearch', 'storage')

        source = mock.Mock()
        destination = mock.Mock()
        bounds = {
            'begin': datetime.datetime(2024, 1, 1, 0, 0),
            'end': datetime.datetime(2024, 1, 2, 0, 0),
        }

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=[source, destination]), \
                mock.patch.object(
                    storage_migrate,
                    'resolve_bounds',
                    return_value=bounds), \
                mock.patch.object(
                    storage_migrate,
                    'overwrite_destination') as overwrite_mock, \
                mock.patch.object(
                    storage_migrate,
                    'assert_destination_empty') as empty_mock, \
                mock.patch.object(
                    storage_migrate,
                    'copy_dataframes') as copy_mock, \
                mock.patch.object(
                    storage_migrate,
                    'verify_migration') as verify_mock:
            storage_migrate.StorageMigrationCommand(
                'influxdb',
                overwrite_destination=True,
            ).run()

        overwrite_mock.assert_called_once_with(
            destination,
            bounds['begin'],
            bounds['end'],
        )
        empty_mock.assert_not_called()
        copy_mock.assert_called_once()
        verify_mock.assert_called_once()

    def test_run_uses_discovered_bounds_when_none_are_provided(self):
        self.conf.set_override('version', 2, 'storage')
        self.conf.set_override('backend', 'opensearch', 'storage')

        source = mock.Mock()
        destination = mock.Mock()
        bounds = {
            'begin': datetime.datetime(2024, 1, 1, 0, 0),
            'end': datetime.datetime(2024, 1, 2, 0, 0),
        }

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=[source, destination]), \
                mock.patch.object(
                    storage_migrate,
                    'resolve_bounds',
                    return_value=bounds) as resolve_mock, \
                mock.patch.object(
                    storage_migrate,
                    'assert_destination_empty'), \
                mock.patch.object(
                    storage_migrate,
                    'copy_dataframes'), \
                mock.patch.object(
                    storage_migrate,
                    'verify_migration'):
            storage_migrate.StorageMigrationCommand('influxdb').run()

        resolve_mock.assert_called_once_with(
            source,
            begin=None,
            end=None,
        )

    def test_run_uses_provided_begin_and_discovered_end(self):
        self.conf.set_override('version', 2, 'storage')
        self.conf.set_override('backend', 'opensearch', 'storage')

        source = mock.Mock()
        destination = mock.Mock()
        begin = datetime.datetime(2024, 1, 1, 12, 0)
        bounds = {
            'begin': begin,
            'end': datetime.datetime(2024, 1, 2, 0, 0),
        }

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=[source, destination]), \
                mock.patch.object(
                    storage_migrate,
                    'resolve_bounds',
                    return_value=bounds) as resolve_mock, \
                mock.patch.object(
                    storage_migrate,
                    'assert_destination_empty'), \
                mock.patch.object(
                    storage_migrate,
                    'copy_dataframes'), \
                mock.patch.object(
                    storage_migrate,
                    'verify_migration'):
            storage_migrate.StorageMigrationCommand(
                'influxdb',
                begin=begin,
            ).run()

        resolve_mock.assert_called_once_with(
            source,
            begin=begin,
            end=None,
        )
