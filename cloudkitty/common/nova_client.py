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
from novaclient import client as nclient


def get_nova_client(conf, conf_opts, **kwargs):
    ks_auth = ks_loading.load_auth_from_conf_options(conf, conf_opts)
    session = ks_loading.load_session_from_conf_options(
        conf,
        conf_opts,
        auth=ks_auth)
    return nclient.Client(2, session=session, endpoint_type=conf[conf_opts].endpoint_type)
