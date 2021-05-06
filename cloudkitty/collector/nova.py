# Copyright (c) 2021 StackHPC Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from keystoneauth1 import loading as ks_loading
from oslo_config import cfg
from oslo_log import log
from voluptuous import Schema

from cloudkitty import collector
from cloudkitty.common import nova_client as nova_client_utils
from cloudkitty import dataframe
from cloudkitty import utils as ck_utils
from cloudkitty.utils import tz as tzutils


LOG = log.getLogger(__name__)

COLLECTOR_NOVA_OPTS = 'collector_nova'

CONF = cfg.CONF

collector_nova_opts = ks_loading.get_auth_common_conf_options()
collector_nova_opts.append(
    cfg.StrOpt(
        'endpoint_type',
        default='internal',
        help='Endpoint URL type (defaults to internal)',
    )
)
cfg.CONF.register_opts(collector_nova_opts, COLLECTOR_NOVA_OPTS)
CONF.register_opts(collector_nova_opts, COLLECTOR_NOVA_OPTS)
ks_loading.register_auth_conf_options(CONF, COLLECTOR_NOVA_OPTS)
ks_loading.register_session_conf_options(CONF, COLLECTOR_NOVA_OPTS)


class NovaCollector(collector.BaseCollector):
    collector_name = 'nova'

    def __init__(self, **kwargs):
        super(NovaCollector, self).__init__(**kwargs)
        self._conn = nova_client_utils.get_nova_client(
            CONF, COLLECTOR_NOVA_OPTS)

    @staticmethod
    def check_configuration(conf):
        """Check metrics configuration."""
        conf = collector.BaseCollector.check_configuration(conf)
        metric_schema = Schema(collector.METRIC_BASE_SCHEMA)

        output = {}
        for metric_name, metric in conf.items():
            output[metric_name] = metric_schema(metric)

        return output

    def _format_data(self, metric_name, data):
        """Formats Nova data format to Cloudkitty data format.

        Returns metadata, groupby, qty
        """
        metadata = {}
        for key in self.conf[metric_name]['metadata']:
            metadata[key] = data.get(key)

        groupby = {}
        for key in self.conf[metric_name]['groupby']:
            if key == 'id' or key == 'resource_id':
                groupby[key] = data.get('instance_id')
            elif key == 'project_id':
                groupby[key] = data.get('tenant_id')
            else:
                groupby[key] = data.get(key)

        # The data object has been checked to include metric_name before this
        # function is called, no need for exception handling.
        qty = data[metric_name]
        converted_qty = ck_utils.convert_unit(
            qty,
            self.conf[metric_name]['factor'],
            self.conf[metric_name]['offset'])
        mutated_qty = ck_utils.mutate(converted_qty,
                                      self.conf[metric_name]['mutate'])
        return metadata, groupby, mutated_qty

    # TODO(priteau): This function is called once per metric, which is not
    # optimal since we can end up calling the API with the same parameters
    # multiple times. Fix using memoization?
    def fetch_all(self, metric_name, start, end, scope_id, q_filter=None):
        """Returns metrics to be valorized."""
        groupby = self.conf[metric_name].get('groupby', [])
        metadata = self.conf[metric_name].get('metadata', [])

        # The start and end datetime objects are in the local timezone. Convert
        # them to UTC naive datetime objects before passing them to usage.get()
        # from novaclient, which converts them to strings using isoformat(),
        # for HTTP requests.
        utc_start = tzutils.local_to_utc(start, naive=True)
        utc_end = tzutils.local_to_utc(end, naive=True)

        res = self._conn.usage.get(scope_id, utc_start, utc_end)
        try:
            server_usages = res.server_usages
        except AttributeError:
            # When no data is available, a Usage object is returned without a
            # server_usages attribute.
            server_usages = []

        formatted_resources = []
        for item in server_usages:
            if metric_name in item:
                metadata, groupby, qty = self._format_data(
                    metric_name,
                    item
                )

                formatted_resources.append(dataframe.DataPoint(
                    self.conf[metric_name]['unit'],
                    qty,
                    0,
                    groupby,
                    metadata,
                ))

        return formatted_resources
