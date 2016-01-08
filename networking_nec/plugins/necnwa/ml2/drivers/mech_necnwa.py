# Copyright 2015-2016 NEC Corporation.  All rights reserved.
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

from networking_nec.plugins.necnwa.common import config
from networking_nec.plugins.necnwa.db import api as nwa_db
from networking_nec.plugins.necnwa import necnwa_utils as nwa_utils
from neutron.common import constants
from neutron.common import utils
from neutron.extensions import portbindings
from neutron.extensions import providernet as prov_net
from neutron.plugins.common import constants as n_constants
from neutron.plugins.ml2 import db
from neutron.plugins.ml2 import driver_api as api
from neutron.plugins.ml2.drivers.openvswitch.mech_driver \
    import mech_openvswitch as ovs

from networking_nec._i18n import _LW
from oslo_log import log
from oslo_serialization import jsonutils
import traceback

LOG = log.getLogger(__name__)

"""
local
"""


class NECNWAMechanismDriver(ovs.OpenvswitchMechanismDriver):

    def __init__(self):
        super(NECNWAMechanismDriver, self).__init__()

    def create_port_precommit(self, context):
        try:
            tenant_id, nwa_tenant_id = nwa_utils.get_tenant_info(context)
            network_name, network_id = nwa_utils.get_network_info(context)
            device_owner = context._port['device_owner']
            device_id = context._port['device_id']

            if ((device_owner == constants.DEVICE_OWNER_ROUTER_INTF or
                 device_owner == constants.DEVICE_OWNER_ROUTER_GW)):

                grplst = [res_grp['device_owner'] for res_grp in
                          jsonutils.loads(config.CONF.NWA.ResourceGroup)]
                if device_owner not in grplst:
                    LOG.warning(_LW("resource group miss match. "
                                    "device_owner=%s") % (device_owner))
                    return

                rt_tid = nwa_utils.get_tenant_id_by_router(
                    context.network._plugin_context.session,
                    device_id
                )
                nwa_rt_tid = nwa_utils.get_nwa_tenant_id(rt_tid)
                LOG.debug("rt_tid=%s" % rt_tid)

                nwa_info = nwa_utils.portcontext_to_nwa_info(context)
                nwa_info['tenant_id'] = rt_tid
                nwa_info['nwa_tenant_id'] = nwa_rt_tid
                proxy = context._plugin.get_nwa_proxy(
                    rt_tid, context.network._plugin_context
                )
                proxy.create_tenant_fw(
                    context.network._plugin_context,
                    rt_tid,
                    nwa_rt_tid,
                    nwa_info
                )

                grp = jsonutils.loads(config.CONF.NWA.ResourceGroup)
                for res in grp:
                    if res['device_owner'] == context._port['device_owner']:
                        dummy_segment = db.get_dynamic_segment(
                            context.network._plugin_context.session,
                            network_id,
                            physical_network=res['ResourceGroupName']
                        )

                        LOG.debug("%s" % dummy_segment)
                        if dummy_segment is None:
                            dummy_segment = {
                                api.PHYSICAL_NETWORK: res['ResourceGroupName'],
                                api.NETWORK_TYPE: n_constants.TYPE_VLAN,
                                api.SEGMENTATION_ID: 0
                            }

                            db.add_network_segment(
                                context.network._plugin_context.session,
                                network_id, dummy_segment, is_dynamic=True)

                        LOG.debug("%s" % dummy_segment)
                        context.set_binding(
                            dummy_segment[api.ID],
                            self.vif_type,
                            {portbindings.CAP_PORT_FILTER: True,
                             portbindings.OVS_HYBRID_PLUG: True}
                        )
            else:
                LOG.warning(_LW("device owner missmatch device_owner=%s") %
                            device_owner)
                return

        except Exception as e:
            LOG.error(str(e))
            lines = traceback.format_exc().splitlines()
            for l in lines:
                LOG.error(l)
        return

    def update_port_precommit(self, context):
        new_port = context.current
        orig_port = context.original
        if (
                (new_port['device_id'] == '') and
                (orig_port['device_id'] != '') and
                (new_port['device_owner'] == '') and
                (orig_port['device_owner'] != '')
        ):
            # device_id and device_owner are clear on VM deleted.
            try:
                tenant_id, nwa_tenant_id = nwa_utils.get_tenant_info(context)
                network_name, network_id = nwa_utils.get_network_info(context)
                LOG.debug('original_port={}'.format(context.original))
                LOG.debug('updated_port={}'.format(context.current))
                nwa_info = nwa_utils.portcontext_to_nwa_info(context, True)
                proxy = context._plugin.get_nwa_proxy(tenant_id)
                proxy.delete_general_dev(
                    context.network._plugin_context,
                    tenant_id,
                    nwa_tenant_id,
                    nwa_info
                )
            except Exception as e:
                LOG.error(str(e))
                lines = traceback.format_exc().splitlines()
                for l in lines:
                    LOG.error(l)
        return

    def delete_port_precommit(self, context):

        try:
            tenant_id, nwa_tenant_id = nwa_utils.get_tenant_info(context)
            network_name, network_id = nwa_utils.get_network_info(context)
            device_owner = context._port['device_owner']
            device_id = context._port['device_id']

            LOG.debug("tenant_id=%(tid)s, nwa_tenant_id=%(nid)s, "
                      "device_owner=%(dev)s",
                      {'tid': tenant_id, 'nid': nwa_tenant_id,
                       'dev': device_owner})

            if (
                    (device_owner == constants.DEVICE_OWNER_ROUTER_GW) or
                    (device_owner == constants.DEVICE_OWNER_ROUTER_INTF)
            ):

                rt_tid = nwa_utils.get_tenant_id_by_router(
                    context.network._plugin_context.session,
                    device_id
                )
                nwa_rt_tid = nwa_utils.get_nwa_tenant_id(rt_tid)

                recode = nwa_db.get_nwa_tenant_binding(
                    context.network._plugin_context.session,
                    rt_tid, nwa_rt_tid)

                if recode is None:
                    LOG.debug('nwa tenant not found')
                    return

                nwa_info = nwa_utils.portcontext_to_nwa_info(context)
                nwa_info['tenant_id'] = rt_tid
                nwa_info['nwa_tenant_id'] = nwa_rt_tid
                proxy = context._plugin.get_nwa_proxy(rt_tid)
                proxy.delete_tenant_fw(
                    context.network._plugin_context,
                    rt_tid,
                    nwa_rt_tid,
                    nwa_info
                )

            elif device_owner == constants.DEVICE_OWNER_FLOATINGIP:
                pass
            elif device_owner == '' and device_id == '':
                pass
            else:
                nwa_info = nwa_utils.portcontext_to_nwa_info(context)
                if nwa_info.get('resource_group_name') is None:
                    LOG.debug('resource_group_name is None nwa_info={}'
                              .format(nwa_info))
                    return
                if device_owner == constants.DEVICE_OWNER_DHCP and \
                        device_id == constants.DEVICE_ID_RESERVED_DHCP_PORT:
                    nwa_info['device']['id'] = utils.get_dhcp_agent_device_id(
                        network_id,
                        context._port.get('binding:host_id')
                    )

                proxy = context._plugin.get_nwa_proxy(tenant_id)
                proxy.delete_general_dev(
                    context.network._plugin_context,
                    tenant_id,
                    nwa_tenant_id,
                    nwa_info
                )

        except Exception as e:
            LOG.error(str(e))
            lines = traceback.format_exc().splitlines()
            for l in lines:
                LOG.error(l)

    def try_to_bind_segment_for_agent(self, context, segment, agent):
        network_name, network_id = nwa_utils.get_network_info(context)

        grp = jsonutils.loads(config.CONF.NWA.ResourceGroup)
        for res in grp:
            mappings = agent['configurations'].get('bridge_mappings', {})
            if not res['ResourceGroupName'] in mappings.keys():
                continue

            if res['device_owner'] == context._port['device_owner']:

                dummy_segment = db.get_dynamic_segment(
                    context.network._plugin_context.session,
                    network_id, physical_network=res['ResourceGroupName'])

                if dummy_segment is None:
                    dummy_segment = {
                        api.PHYSICAL_NETWORK: res['ResourceGroupName'],
                        api.NETWORK_TYPE: n_constants.TYPE_VLAN,
                        api.SEGMENTATION_ID: 0
                    }
                    db.add_network_segment(
                        context.network._plugin_context.session,
                        network_id, dummy_segment, is_dynamic=True)

                context.set_binding(dummy_segment[api.ID],
                                    self.vif_type,
                                    {portbindings.CAP_PORT_FILTER: True,
                                     portbindings.OVS_HYBRID_PLUG: True})

                self._bind_port_nwa(context)
                return True

        LOG.warning(_LW("binding segment not found for agent=%s") % agent)

        return super(NECNWAMechanismDriver,
                     self).try_to_bind_segment_for_agent(
                         context, segment, agent)

    def bind_port(self, context):
        super(NECNWAMechanismDriver, self).bind_port(context)

    def _bind_port_nwa(self, context):
        try:

            tenant_id, nwa_tenant_id = nwa_utils.get_tenant_info(context)
            network_name, network_id = nwa_utils.get_network_info(context)
            device_id = context._port['device_id']
            device_owner = context._port['device_owner']
            port_id = context._port['id']
            mac_address = context._port['mac_address']

            if (
                    (device_owner == constants.DEVICE_OWNER_ROUTER_GW) or
                    (device_owner == constants.DEVICE_OWNER_ROUTER_INTF)
            ):
                return

            subnet_ids = []
            if 'fixed_ips' in context._port:
                for fixed_ip in context._port['fixed_ips']:
                    subnet_ids.append(fixed_ip['subnet_id'])

            segmentation_id = 0
            if prov_net.PHYSICAL_NETWORK in context.network.current:
                if context.network.current[prov_net.SEGMENTATION_ID] is None:
                    LOG.debug("current segmentation_id is None")
                else:
                    segmentation_id = \
                        context.network.current[prov_net.SEGMENTATION_ID]
                    LOG.debug("current segmentation_id = %d" %
                              segmentation_id)
            else:
                for provider in context.network.current['segments']:
                    physical_network = \
                        nwa_utils.get_physical_network(device_owner)
                    if provider[prov_net.PHYSICAL_NETWORK] != physical_network:
                        continue
                    segmentation_id = provider[prov_net.SEGMENTATION_ID]
                    LOG.debug("provider segmentation_id = %d" %
                              segmentation_id)
                    break

            LOG.debug("_bind_port_nwa %(tenant_id)s %(network_name)s "
                      "%(network_id)s %(device_id)s %(device_owner)s "
                      "%(port_id)s %(mac_address)s %(subnet_ids)s "
                      "%(segmentation_id)d",
                      {'tenant_id': tenant_id,
                       'network_name': network_name,
                       'network_id': network_id,
                       'device_id': device_id,
                       'device_owner': device_owner,
                       'port_id': port_id,
                       'mac_address': mac_address,
                       'subnet_ids': subnet_ids,
                       'segmentation_id': segmentation_id})

            recode = nwa_db.get_nwa_tenant_binding(
                context.network._plugin_context.session,
                tenant_id, nwa_tenant_id)

            LOG.debug('bind_port recode=%(recode)s', {'recode': recode})

            nwa_info = nwa_utils.portcontext_to_nwa_info(context)
            proxy = context._plugin.get_nwa_proxy(tenant_id)
            proxy.create_general_dev(
                context.network._plugin_context,
                tenant_id,
                nwa_tenant_id,
                nwa_info
            )

        except Exception as e:
            LOG.error(str(e))
            lines = traceback.format_exc().splitlines()
            for l in lines:
                LOG.error(l)
