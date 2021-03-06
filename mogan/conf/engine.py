# Copyright 2016 Huawei Technologies Co.,LTD.
# All Rights Reserved.
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

from oslo_config import cfg

from mogan.common.i18n import _

opts = [
    cfg.IntOpt('workers_pool_size',
               default=100,
               help=_('The size of the workers greenthread pool.')),
    cfg.StrOpt('api_url',
               help=_('URL of Mogan API service. If not set mogan can '
                      'get the current value from the keystone service '
                      'catalog.')),
    cfg.IntOpt('periodic_max_workers',
               default=8,
               help=_('Maximum number of worker threads that can be started '
                      'simultaneously by a periodic task. Should be less '
                      'than RPC thread pool size.')),
    cfg.IntOpt('update_resources_interval',
               default=60,
               help=_('Interval between syncing the resources from underlying '
                      'hypervisor, in seconds.')),
    cfg.StrOpt('default_schedule_zone',
               help="""
Availability zone to use when user doesn't specify one.

This option is used by the scheduler to determine which availability
zone to place a new server into if the user did not specify one at the
time of server boot request.

Possible values:

* Any string representing an availability zone name
* Default value is None.
"""),
    cfg.IntOpt('sync_power_state_pool_size',
               default=1000,
               help=_("Number of greenthreads available for use to sync "
                      "power states. Any positive integer representing "
                      "greenthreads count.")),
    cfg.IntOpt('sync_power_state_interval',
               default=600,
               help=_("Interval to sync power states between the database "
                      "and Ironic, in seconds.")),
    cfg.IntOpt('sync_maintenance_interval',
               default=600,
               help=_("Interval to sync maintenance states between the "
                      "database and Ironic, in seconds.")),
    cfg.StrOpt('engine_driver',
               default='ironic.IronicDriver',
               choices=['ironic.IronicDriver'],
               help=_("Which driver to use, default to ironic driver.")),
    cfg.IntOpt('default_root_partition',
               default=10,
               help=_("The default root partition size(GB) for partition "
                      "images."))
]


def register_opts(conf):
    conf.register_opts(opts, group='engine')
