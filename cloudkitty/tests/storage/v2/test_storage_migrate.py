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

import testscenarios

from cloudkitty.cli import storage_migrate
from cloudkitty import storage
from cloudkitty.tests.storage.v2 import es_utils
from cloudkitty.tests.storage.v2 import influx_utils
from cloudkitty.tests.storage.v2 import loki_utils
from cloudkitty.tests.storage.v2 import opensearch_utils
from cloudkitty.tests import TestCase
from cloudkitty.tests import utils as test_utils
from cloudkitty.utils import tz as tzutils


_ES_CLIENT_PATH = ('cloudkitty.storage.v2.elasticsearch'
                   '.client.ElasticsearchClient')
_INFLUX_CLIENT_PATH = 'cloudkitty.storage.v2.influx.InfluxClient'
_LOKI_CLIENT_PATH = 'cloudkitty.storage.v2.loki.client.LokiClient'
_OS_CLIENT_PATH = ('cloudkitty.storage.v2.opensearch'
                   '.client.OpenSearchClient')


class StorageMigrationFlowTest(TestCase):
    storage_scenarios = [
        ('influxdb_to_opensearch', dict(
            source_backend='influxdb',
            destination_backend='opensearch',
        )),
        ('influxdb_to_elasticsearch', dict(
            source_backend='influxdb',
            destination_backend='elasticsearch',
        )),
        ('influxdb_to_loki', dict(
            source_backend='influxdb',
            destination_backend='loki',
        )),
        ('loki_to_opensearch', dict(
            source_backend='loki',
            destination_backend='opensearch',
        )),
    ]

    @classmethod
    def generate_scenarios(cls):
        cls.scenarios = testscenarios.multiply_scenarios(
            cls.scenarios,
            cls.storage_scenarios,
        )

    @mock.patch(_ES_CLIENT_PATH,
                new=es_utils.FakeElasticsearchClient)
    @mock.patch(_INFLUX_CLIENT_PATH,
                new=influx_utils.FakeInfluxClient)
    @mock.patch(_LOKI_CLIENT_PATH,
                new=loki_utils.FakeLokiClient)
    @mock.patch(_OS_CLIENT_PATH,
                new=opensearch_utils.FakeOpenSearchClient)
    @mock.patch('cloudkitty.utils.load_conf', new=test_utils.load_conf)
    def setUp(self):
        super(StorageMigrationFlowTest, self).setUp()
        self.conf.set_override('version', '2', 'storage')
        self.conf.set_override('backend', self.destination_backend, 'storage')
        self.source_storage = storage.get_storage(
            backend=self.source_backend,
            version=2,
            conf=test_utils.load_conf(),
        )
        self.destination_storage = storage.get_storage(
            backend=self.destination_backend,
            version=2,
            conf=test_utils.load_conf(),
        )
        self.source_storage.init()
        self.destination_storage.init()
        self.data = []
        self._init_source_data()

    def _init_source_data(self):
        project_ids = ['project-a', 'project-b']
        start_base = tzutils.utc_to_local(datetime.datetime(2024, 1, 1))
        for index in range(3):
            start = tzutils.add_delta(
                start_base,
                datetime.timedelta(hours=index),
            )
            end = tzutils.add_delta(start, datetime.timedelta(hours=1))
            frame = test_utils.generate_v2_storage_data(
                project_ids=project_ids,
                start=start,
                end=end,
            )
            self.data.append(frame)
            self.source_storage.push([frame])

    def _get_storage_side_effect(self, **kwargs):
        backend = kwargs.get('backend') or self.conf.storage.backend
        if backend == self.source_backend:
            return self.source_storage
        if backend == self.destination_backend:
            return self.destination_storage
        raise AssertionError('Unexpected backend %s' % backend)

    def _assert_totals_equal(self, source_total, destination_total):
        self.assertEqual(
            storage_migrate._normalize_total(source_total),
            storage_migrate._normalize_total(destination_total),
        )

    def test_full_fake_backend_migration_succeeds(self):
        command = storage_migrate.StorageMigrationCommand(
            self.source_backend,
            page_size=1,
        )

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=self._get_storage_side_effect):
            command.run()

        begin = self.data[0].start
        end = self.data[-1].end
        source_total = self.source_storage.total(
            begin=begin,
            end=end,
            paginate=False,
        )
        destination_total = self.destination_storage.total(
            begin=begin,
            end=end,
            paginate=False,
        )
        self._assert_totals_equal(source_total, destination_total)

    def test_rerunning_against_non_empty_destination_fails(self):
        self.destination_storage.push([self.data[0]])
        command = storage_migrate.StorageMigrationCommand(self.source_backend)

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=self._get_storage_side_effect):
            self.assertRaises(
                storage_migrate.StorageMigrationError,
                command.run,
            )

    def test_rerunning_against_non_empty_destination_overwrites(self):
        self.destination_storage.push([self.data[0]])
        command = storage_migrate.StorageMigrationCommand(
            self.source_backend,
            overwrite_destination=True,
        )

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=self._get_storage_side_effect):
            command.run()

        begin = self.data[0].start
        end = self.data[-1].end
        source_total = self.source_storage.total(
            begin=begin,
            end=end,
            paginate=False,
        )
        destination_total = self.destination_storage.total(
            begin=begin,
            end=end,
            paginate=False,
        )
        self._assert_totals_equal(source_total, destination_total)

    def test_begin_end_limit_the_migration_scope(self):
        begin = self.data[1].start
        end = self.data[1].end
        command = storage_migrate.StorageMigrationCommand(
            self.source_backend,
            begin=begin,
            end=end,
            page_size=1,
        )

        with mock.patch.object(
                storage_migrate.storage,
                'get_storage',
                side_effect=self._get_storage_side_effect):
            command.run()

        source_total = self.source_storage.total(
            begin=begin,
            end=end,
            paginate=False,
        )
        destination_total = self.destination_storage.total(
            begin=begin,
            end=end,
            paginate=False,
        )
        self._assert_totals_equal(source_total, destination_total)


StorageMigrationFlowTest.generate_scenarios()
