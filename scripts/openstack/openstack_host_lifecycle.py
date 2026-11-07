"""Explicit lifecycle coordinator. Never recover guests or infer administrator intent."""
import json
import time
from openstack_container_engine import write


class LifecycleWorker:
    def lifecycle_catalog(self, name, *, request_id=None):
        state = self.observation(name,'lifecycle','lifecycle-observe')
        readiness = self.observation(name,'launch','launch-report')
        host = self.cfg['hosts'][name].get('inventory_name', name)
        from openstack_spvm.cluster_flow import bounded
        services = [s for s in bounded(self.c.compute.services(binary='nova-compute')) if s.host == host]
        service = services[0] if len(services) == 1 else None
        blockers = list(state['reasons'])
        if service is None or service.state != 'up':
            blockers.append('COMPUTE_NOT_UP')
        expected = self.cfg['hosts'][name]['launch_mode']
        if (readiness.get('mode') != expected or
                not readiness.get('debug_ready' if expected == 'untrusted' else 'cc_ready')):
            blockers.append('LAUNCH_MODE_NOT_READY')
        try:
            technical_ready=self.observation(name,'technical','lifecycle-readiness')=={'ready':True}
        except Exception:
            technical_ready=False
        if not technical_ready:blockers.append('HELPER_NETWORK_OR_RUNTIME_NOT_READY')
        for path in self.root.glob('*/transaction.json'):
            if path.parent.name == request_id:
                continue
            record = json.loads(path.read_bytes())
            if record.get('host') in (None, host) and record.get('status') not in ('SUCCEEDED', 'REFUSED'):
                blockers.append('HOST_TRANSACTION_REVIEW_REQUIRED')
        observed_at=getattr(self,'_observations',{}).get(name,{}).get('observed_at',int(time.time()))
        catalog = {'schema_version': 1, 'project_id': self.config['project_id'], 'host': host,'observed_at':observed_at,
                   'generation': state['generation'], 'status': state['status'],
                   'resources': state['resources'], 'readiness': readiness,
                   'technical_ready': technical_ready,
                   'nova_status': service.status if service else 'unknown',
                   'blockers': sorted(set(blockers))}
        return catalog, state, service

    def sync_lifecycle(self):
        from openstack_spvm import host_lifecycle as model
        for name in self.config['executors']:
            catalog, state, _ = self.lifecycle_catalog(name)
            self.publish(model.PREFIX + state['resources']['compute_uuid'] + '-catalog', catalog)

    def process_lifecycle(self, entry):
        from openstack_spvm import host_lifecycle as model, cluster_flow, secret_reader
        meta = secret_reader.read_json(self.c.key_manager, '/secrets/' + entry)
        creator = meta.get('creator_id')
        if not self.authorized_creator(creator):
            return
        request_id = meta['name'].removeprefix(model.PREFIX + 'request-')
        secret_reader.canonical_uuid(request_id)
        body = cluster_flow.parse_json(cluster_flow.record(self.c.key_manager, meta['name'], [creator])[1])
        if (type(body) is not dict or set(body) != {'schema_version', 'project_id', 'user_id', 'request_id', 'host', 'generation', 'enabled'}
                or type(body['schema_version']) is not int or body['schema_version'] != 1 or body['project_id'] != self.config['project_id']
                or body['user_id'] != creator or body['request_id'] != request_id):
            raise ValueError('lifecycle creator/scope differs')
        intent = model.normalize({k: body[k] for k in ('request_id', 'host', 'generation', 'enabled')})
        result_name = model.PREFIX + 'result-' + request_id
        if cluster_flow.record(self.c.key_manager, result_name, [self.publisher]):
            return
        folder = self.root / request_id
        if folder.exists():
            self.publish(result_name, {'request_id': request_id, 'project_id': self.config['project_id'],
                'status': 'REVIEW_REQUIRED', 'message': 'Interrupted lifecycle operation; no automatic replay.'})
            return
        folder.mkdir(mode=0o700)
        record = {'request_id': request_id, 'host': body['host'], 'kind': 'lifecycle',
                  'status': 'PROCESSING', 'phase': 'PREFLIGHT', 'intent': intent}
        journal = folder / 'transaction.json'
        write(journal, record)
        attempted = False
        host = None
        service = None
        try:
            matches = [n for n in self.config['executors'] if self.cfg['hosts'][n].get('inventory_name', n) == body['host']]
            if len(matches) != 1:
                raise ValueError('host not delegated')
            host = matches[0]
            catalog, before, service = self.lifecycle_catalog(host, request_id=request_id)
            if catalog['generation'] != body['generation']:
                raise ValueError('resource generation changed')
            if body['enabled']:
                if catalog['blockers'] or catalog['status'] == 'UNCONFIGURED' or service.status != 'disabled':
                    raise ValueError('complete, healthy, disabled host required for Enable')
                observed = self.local(host, 'observe')
                self.verify(observed, observed['current'])
                gpu=self.gpu_local(host,'observe')
                if gpu['blockers']:raise ValueError('GPU configuration is not consistent')
                self.verify_gpus(host,gpu['inventory'],require_empty=False)
                readiness = self.channel.call(host, 'lifecycle-readiness')
                if readiness != {'ready': True}:
                    raise ValueError('host helper/network/runtime readiness failed')
            record['phase'] = 'APPLYING_HOST_FLAG'
            write(journal, record)
            attempted = True
            if body['enabled']:
                prepared=self.channel.call(host,'lifecycle-prepare-enable',request_id=request_id,before=before)
                record['phase']='ENABLING_NOVA_WITH_HOST_ADMISSION_FENCED';write(journal,record)
                self.c.compute.enable_service(service)
                from openstack_spvm.cluster_flow import bounded
                current=[s for s in bounded(self.c.compute.services(binary='nova-compute')) if s.host==body['host']]
                if len(current)!=1 or current[0].id!=service.id or current[0].status!='enabled' or current[0].state!='up':
                    raise ValueError('Nova enable was not confirmed')
                record['phase']='COMMITTING_HOST_ENABLE';write(journal,record)
                # Admission becomes possible only at this durable local commit.
                # A lost Nova acknowledgement leaves the local transaction fenced.
                after=self.channel.call(host,'lifecycle-commit-enable',request_id=request_id,before=prepared)
            else:
                after=self.channel.call(host,'lifecycle-disable',request_id=request_id,before=before)
                if service is not None:
                    self.c.compute.disable_service(service, disabled_reason='SPVM administrator disabled host')
            record['host_generation']=after['generation']
            check = self.channel.call(host, 'lifecycle-observe')
            if check != after or check['resources']['enabled'] != body['enabled']:
                raise ValueError('host lifecycle changed before acknowledgement')
            result = {'request_id': request_id, 'project_id': self.config['project_id'], 'status': 'SUCCEEDED',
                      'message': 'Host enabled.' if body['enabled'] else 'New admissions disabled. Existing guests and accepted operations were not stopped.'}
        except Exception as error:
            record['error_type'] = type(error).__name__
            confirmed=False
            if attempted and host is not None:
                try:
                    current = self.channel.call(host, 'lifecycle-observe')
                    # Reconcile a lost final commit response by GET only, never
                    # replay prepare/commit or infer completion from Nova alone.
                    if body['enabled'] and record['phase']=='COMMITTING_HOST_ENABLE' and current['status']=='ENABLED' and current['resources']['last_request_id']==request_id and current['resources']['transaction'] is None:
                        from openstack_spvm.cluster_flow import bounded
                        actual=[s for s in bounded(self.c.compute.services(binary='nova-compute')) if s.host==body['host']]
                        confirmed=len(actual)==1 and actual[0].id==service.id and actual[0].state=='up' and actual[0].status=='enabled'
                    if not confirmed:
                        self.channel.call(host, 'lifecycle-disable', request_id=request_id, before=current)
                        record['disabled_confirmed'] = True
                except Exception:
                    record['disabled_confirmed'] = False
                if not confirmed and service is not None:
                    try:
                        self.c.compute.disable_service(service, disabled_reason='SPVM lifecycle review required')
                        record['nova_quarantine_confirmed']=True
                    except Exception:
                        record['nova_quarantine_confirmed']=False
            result = {'request_id': request_id, 'project_id': self.config['project_id'],
                      'status': 'SUCCEEDED' if confirmed else 'REVIEW_REQUIRED' if attempted else 'REFUSED',
                      'message': 'Durable Enable commit and Nova scheduling confirmed read-only; no mutation replayed.' if confirmed else 'Lifecycle request failed. Refresh host state and inspect the private journal; no automatic replay.'}
        record.update(result)
        write(journal, record)
        self.publish(result_name, result)
