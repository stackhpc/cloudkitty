# Copyright 2017 GohighSec.
# All Rights Reserved.
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

from oslo_policy import policy

RULE_ADMIN_OR_OWNER = 'rule:admin_or_owner'
ROLE_ADMIN = 'role:admin'
UNPROTECTED = ''

rules = [
    policy.RuleDefault(
        name='context_is_admin',
        check_str='role:admin'),
    policy.RuleDefault(
        name='admin_or_owner',
        check_str='is_admin:True or '
                  '(role:admin and is_admin_project:True) or '
                  'project_id:%(project_id)s'),
    policy.RuleDefault(
        name='default',
        check_str=UNPROTECTED)
]


def list_rules():
    return rules
