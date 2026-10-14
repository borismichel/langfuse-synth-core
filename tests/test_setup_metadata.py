"""Versioned setup metadata is validated at the public Manifest boundary."""
from copy import deepcopy
from pathlib import Path
import pytest
import yaml
from jsonschema import Draft7Validator
from langfuse_synth_core.authoring.validate import load_schema, validate_doc


def manifest():
    doc = yaml.safe_load((Path(__file__).parent / 'fixtures/manifests/ev-subsidy-regression.usecase.yaml').read_text())
    doc['config_schema'] = {'type':'object','properties': {
        'generation.target_traces': {'type':'integer','minimum':100,'maximum':6000,'default':800},
        'generation.seed': {'type':'integer','minimum':0,'default':42},
        'incident.enabled': {'type':'boolean','default':True},
    }}
    doc['setup'] = {'version':1,'fields':{'incident.enabled':{'group':'scenario'}},
        'presets':{'quick_rehearsal':{'label':'Quick rehearsal','values':{'generation.target_traces':300,'incident.enabled':False,'generation.seed':0}}},
        'host_defaults':{'self_hosted':{'generation.target_traces':4000}}}
    return doc


def test_declared_setup_accepts_typed_false_zero_and_host_defaults():
    assert validate_doc(manifest(), Draft7Validator(load_schema())) == []


@pytest.mark.parametrize('key,value', [('undeclared.key',300),('generation.target_traces',True),('generation.target_traces',100.5),('generation.target_traces',99),('generation.target_traces',float('inf')),('incident.enabled','false')])
def test_invalid_preset_values_rejected(key,value):
    doc=manifest()
    doc['setup']['presets']['quick_rehearsal']['values']={key:value}
    assert validate_doc(doc,Draft7Validator(load_schema()))


def test_unknown_field_and_unsupported_host_rejected():
    doc=manifest()
    doc['setup']['fields']['secret.key']={'group':'scenario'}
    assert validate_doc(doc,Draft7Validator(load_schema()))
    doc=manifest()
    doc['setup']['host_defaults']={'cloud_jp':{'generation.seed':0}}
    assert validate_doc(doc,Draft7Validator(load_schema()))


@pytest.mark.parametrize('schedule',[[], 'UTC', {'history':42}])
def test_malformed_schedule_is_a_validation_error_not_an_exception(schedule):
    doc=manifest()
    doc['setup']['schedule']=schedule
    assert validate_doc(doc,Draft7Validator(load_schema()))


@pytest.mark.parametrize('field,value',[('target','cloud_eu'),('target',['cloud_eu']),('setup',{'version':1,'schedule':{'history':{'field':[]}},'presets':{'quick_rehearsal':{'label':'Quick rehearsal','values':{'generation.seed':0}}}})])
def test_malformed_contract_objects_return_validation_errors(field,value):
    doc=manifest()
    doc[field]=value
    assert validate_doc(doc,Draft7Validator(load_schema()))
