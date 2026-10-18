"""Portable compression functions represented as validated data, never remote Python."""
import hashlib
import json
import math
from pathlib import Path


def vector(value):
    if not isinstance(value,list) or len(value)!=2:
        raise ValueError('QT vectors must contain two finite numbers')
    if any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>100 for v in value):
        raise ValueError('QT values must be finite and within [-100,100]')
    return [float(v) for v in value]


def validate(spec):
    if not isinstance(spec,dict) or set(spec)!={'version','text_limit','sentiment','eeg_mean','eeg_std'}:
        raise ValueError('Unsupported compression specification')
    if spec['version']!=1 or type(spec['text_limit']) is not int or not 1<=spec['text_limit']<=4096:
        raise ValueError('Invalid compression version or text limit')
    for name in ['sentiment','eeg_mean','eeg_std']:
        value=spec[name]
        if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>100:
            raise ValueError('Invalid compression parameter: '+name)
    if spec['eeg_std']<0: raise ValueError('Standard deviation cannot be negative')
    return spec


def fingerprint(spec):
    validate(spec)
    return hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def compression(spec):
    spec=dict(validate(spec))
    def apply(psi):
        if psi.source=='text': return {'summary':str(psi.raw_data)[:spec['text_limit']],'sentiment':spec['sentiment']}
        if psi.source=='eeg': return {'mean':spec['eeg_mean'],'std':spec['eeg_std']}
        return {'raw':psi.raw_data}
    return apply


def write_json(path,data):
    path=Path(path)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,allow_nan=False),encoding='utf-8')
    temp.replace(path)
