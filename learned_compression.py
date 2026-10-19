"""Lossless compression with a small NumPy neural codec selector and held-out evaluation."""
import argparse
import collections
import hashlib
import json
import lzma
import math
from pathlib import Path
import random
import struct
import time
import zlib
import numpy as np
from shared_evolution import write_json

CODECS=('raw','zlib','lzma')
HEADER=struct.Struct('!4sBII')
MAX_BYTES=64*1024*1024


def encode(data,codec):
    if len(data)>MAX_BYTES:raise ValueError('Input exceeds the 64 MiB limit')
    if codec not in CODECS:raise ValueError('Unknown codec')
    payload=data if codec=='raw' else zlib.compress(data,9) if codec=='zlib' else lzma.compress(data,preset=1)
    return HEADER.pack(b'XCB1',CODECS.index(codec),len(data),zlib.crc32(data))+payload


def decode(packet):
    if len(packet)<HEADER.size:raise ValueError('Truncated archive')
    magic,codec,length,checksum=HEADER.unpack(packet[:HEADER.size]);payload=packet[HEADER.size:]
    if magic!=b'XCB1' or codec>=len(CODECS) or length>MAX_BYTES:raise ValueError('Unsupported archive or size limit')
    if codec==0:result=payload
    elif codec==1:
        decoder=zlib.decompressobj();result=decoder.decompress(payload,length+1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:raise ValueError('Invalid zlib archive')
    else:
        decoder=lzma.LZMADecompressor(memlimit=64*1024*1024);result=decoder.decompress(payload,max_length=length+1)
        if not decoder.eof or decoder.unused_data:raise ValueError('Invalid lzma archive')
    if len(result)!=length or zlib.crc32(result)!=checksum:raise ValueError('Reconstruction checksum or length mismatch')
    return result


def features(data):
    sample=data[:4096];n=len(sample)
    if not n:return np.zeros(10)
    counts=np.bincount(np.frombuffer(sample,dtype=np.uint8),minlength=256)
    p=counts[counts>0]/n
    entropy=float(-(p*np.log2(p)).sum())/8
    grams=[sample[i:i+4] for i in range(max(0,n-3))]
    duplicate=1-len(set(grams))/len(grams) if grams else 0
    return np.array([min(math.log2(len(data)+1)/24,1),entropy,np.count_nonzero(counts)/256,
        sum(a==b for a,b in zip(sample,sample[1:]))/max(1,n-1),
        sum(32<=b<127 for b in sample)/n,counts[0]/n,counts[32]/n,counts[10]/n,duplicate,
        sum(b>=128 for b in sample)/n],dtype=float)


def corpus(seed,count):
    """Deterministic mixed synthetic files; split seeds and payload hashes are recorded."""
    rng=random.Random(seed);docs=[]
    words='worker cluster storage message compression service result healthy node packet'.split()
    for index in range(count):
        size=rng.choice([64,256,1024,4096,16384]);kind=rng.randrange(6)
        if kind==0:raw=rng.randbytes(size)
        elif kind==1:
            block=rng.randbytes(rng.choice([4,16,64]));raw=(block*(size//len(block)+1))[:size]
        elif kind==2:
            lines=[]
            while sum(map(len,lines))<size:
                lines.append(json.dumps({'node':rng.choice(['alpha','beta','gamma']),
                    'event':rng.choice(['heartbeat','message','completed','started']),
                    'value':rng.randrange(10000),'healthy':True},sort_keys=True).encode()+b'\n')
            raw=b''.join(lines)[:size]
        elif kind==3:raw=(' '.join(rng.choices(words,k=size//4+1))).encode()[:size]
        elif kind==4:
            raw=bytearray(size)
            for pos in range(0,size,31):raw[pos]=rng.randrange(256)
            raw=bytes(raw)
        else:raw=('\u03bb\u03b1\u03bc\u03b2\u03b4\u03b1 \u8282\u70b9 \U0001f426 '+rng.choice(words)+'\n').encode()*(size//8+1);raw=raw[:size]
        docs.append(f'{seed}:{index}:'.encode()+raw)
    return docs


def prepare(docs):
    x=np.array([features(d) for d in docs])
    sizes=np.array([[len(encode(d,c)) for c in CODECS] for d in docs],dtype=int)
    return x,sizes,np.argmin(sizes,axis=1)


def file_corpus(directory):
    root=Path(directory).resolve()
    if not root.is_dir():raise ValueError('Corpus must be a directory')
    documents={}
    for path in sorted(root.rglob('*')):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:sample=stream.read(65536)
            if sample:documents[hashlib.sha256(sample).hexdigest()]=sample
            if len(documents)>=256:break
    if len(documents)<20:raise ValueError('Provide at least 20 distinct nonempty files for file-level train/validation/test splits')
    ordered=[documents[key] for key in sorted(documents)]
    first=int(len(ordered)*.7);second=int(len(ordered)*.85)
    ident=hashlib.sha256(''.join(sorted(documents)).encode()).hexdigest()
    return (ordered[:first],ordered[first:second],ordered[second:]),'files-'+ident


class NeuralSelector:
    """10 inputs -> 24 tanh neurons -> 3 softmax outputs; trained by Adam/backprop."""
    def __init__(self,seed=41,parameters=None):
        rng=np.random.default_rng(seed)
        self.parameters=parameters or {'w1':rng.normal(0,.25,(10,24)),'b1':np.zeros(24),
            'w2':rng.normal(0,.25,(24,3)),'b2':np.zeros(3)}
    def probabilities(self,x):
        p=self.parameters;h=np.tanh(x@p['w1']+p['b1']);logits=h@p['w2']+p['b2']
        exp=np.exp(logits-logits.max(axis=1,keepdims=True));return exp/exp.sum(axis=1,keepdims=True)
    def predict(self,x):return np.argmax(self.probabilities(x),axis=1)
    def loss(self,x,y):return float(-np.log(np.maximum(self.probabilities(x)[np.arange(len(y)),y],1e-12)).mean())
    def gradients(self,x,y):
        p=self.parameters;h=np.tanh(x@p['w1']+p['b1']);delta=self.probabilities(x)
        delta[np.arange(len(y)),y]-=1;delta/=len(y)
        hidden=(delta@p['w2'].T)*(1-h*h)
        return {'w2':h.T@delta,'b2':delta.sum(axis=0),'w1':x.T@hidden,'b1':hidden.sum(axis=0)}
    def dump(self):return {'version':1,'architecture':'10-24-3-tanh-softmax','parameters':{k:v.tolist() for k,v in self.parameters.items()}}
    @classmethod
    def load(cls,path):
        data=json.loads(Path(path).read_text());expected={'w1':(10,24),'b1':(24,),'w2':(24,3),'b2':(3,)}
        if data.get('version')!=1 or data.get('architecture')!='10-24-3-tanh-softmax':raise ValueError('Unsupported neural model')
        if set(data.get('parameters',{}))!=set(expected):raise ValueError('Invalid neural model parameters')
        parameters={k:np.asarray(data['parameters'][k],dtype=float) for k in expected}
        if any(v.shape!=expected[k] or not np.isfinite(v).all() or np.abs(v).max()>1000 for k,v in parameters.items()):
            raise ValueError('Invalid neural weights')
        return cls(parameters=parameters)


def selected_bytes(model,x,sizes):
    picks=model.predict(x);return int(np.minimum(sizes[np.arange(len(x)),picks],sizes[:,0]).sum())


def fit(model,training,validation,epochs=250):
    x,_,y=training;vx,vs,vy=validation
    initial_loss=model.loss(x,y)
    best={k:v.copy() for k,v in model.parameters.items()}
    best_score=(selected_bytes(model,vx,vs),model.loss(vx,vy))
    momentum={k:np.zeros_like(v) for k,v in model.parameters.items()}
    variance={k:np.zeros_like(v) for k,v in model.parameters.items()}
    curve=[];selected_epoch=0
    for epoch in range(1,epochs+1):
        gradients=model.gradients(x,y)
        for key,g in gradients.items():
            momentum[key]=.9*momentum[key]+.1*g;variance[key]=.999*variance[key]+.001*g*g
            model.parameters[key]-=.01*(momentum[key]/(1-.9**epoch))/(np.sqrt(variance[key]/(1-.999**epoch))+1e-8)
        if epoch%5==0:
            score=(selected_bytes(model,vx,vs),model.loss(vx,vy))
            curve.append({'epoch':epoch,'train_loss':model.loss(x,y),'validation_loss':score[1],'validation_bytes':score[0]})
            if score<best_score:
                best_score=score;best={k:v.copy() for k,v in model.parameters.items()};selected_epoch=epoch
    model.parameters=best
    return {'initial_train_loss':initial_loss,'selected_train_loss':model.loss(x,y),
        'selected_epoch':selected_epoch,'epochs_run':epochs,'curve':curve}


def compress(data,model):
    codec=CODECS[int(model.predict(features(data)[None,:])[0])]
    packet=encode(data,codec)
    return encode(data,'raw') if len(packet)>len(data)+HEADER.size else packet


def run_benchmark(folder,train_count=180,eval_count=60,epochs=250,corpus_path=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);start=time.perf_counter()
    report_path=folder/'learning_metrics.json';model_path=folder/'selector_model.json'
    previous=json.loads(report_path.read_text()) if report_path.exists() else {}
    generation=previous.get('generation',0)+1
    if corpus_path:
        (train_docs,val_docs,test_docs),dataset_id=file_corpus(corpus_path)
        dataset='user-selected file corpus; distinct files split by content hash; first 64 KiB per file, at most 256 files'
    else:
        train_docs=corpus(1000+generation,train_count);val_docs=corpus(200000,eval_count);test_docs=corpus(300000,eval_count)
        dataset_id='synthetic-v1';dataset='mixed synthetic files; separate seeded train/validation/test splits'
    if previous and previous.get('dataset_id',dataset_id)!=dataset_id:
        raise ValueError('Dataset changed; use a fresh state folder to prevent train/test leakage from an older model')
    hashes=[{hashlib.sha256(d).hexdigest() for d in docs} for docs in [train_docs,val_docs,test_docs]]
    if any(hashes[a]&hashes[b] for a,b in [(0,1),(0,2),(1,2)]):raise ValueError('Dataset split overlap')
    training=prepare(train_docs);validation=prepare(val_docs)
    model=NeuralSelector.load(model_path) if model_path.exists() else NeuralSelector()
    incumbent=NeuralSelector(parameters={k:v.copy() for k,v in model.parameters.items()})
    optimization=fit(model,training,validation,epochs)
    # Test measurements start only after checkpoint selection is complete.
    tx,ts,ty=prepare(test_docs);predictions=model.predict(tx)
    packets=[compress(d,model) for d in test_docs]
    if any(decode(p)!=d for p,d in zip(packets,test_docs)):raise ValueError('Lossless round-trip failed')
    total=sum(map(len,test_docs));packed=sum(map(len,packets))
    fallback_sizes=np.minimum(ts,ts[:,0,None])
    fixed_codec=int(np.argmin(np.minimum(training[1],training[1][:,0,None]).sum(axis=0)))
    initial=NeuralSelector()
    report={'version':1,'generation':generation,'dataset':dataset,'dataset_id':dataset_id,
        'counts':{'train':len(train_docs),'validation':len(val_docs),'test':len(test_docs)},
        'split_sha256':[hashlib.sha256(''.join(sorted(h)).encode()).hexdigest() for h in hashes],
        'architecture':'10 inputs, 24 tanh hidden neurons, 3 softmax codec outputs',
        'parameter_count':sum(v.size for v in model.parameters.values()),'optimization':optimization,
        'test':{'original_bytes':total,'compressed_bytes':packed,'ratio':packed/total,
            'saving_pct':100*(1-packed/total),'untrained_bytes':selected_bytes(initial,tx,ts),
            'incumbent_bytes':selected_bytes(incumbent,tx,ts),'fixed_zlib_bytes':int(ts[:,1].sum()),
            'zlib_with_raw_fallback_bytes':int(fallback_sizes[:,1].sum()),
            'fixed_lzma_bytes':int(fallback_sizes[:,2].sum()),'best_fixed_train_codec':CODECS[fixed_codec],
            'best_fixed_train_codec_bytes':int(fallback_sizes[:,fixed_codec].sum()),
            'oracle_bytes':int(ts.min(axis=1).sum()),'accuracy':float(np.mean(predictions==ty)),
            'untrained_accuracy':float(np.mean(initial.predict(tx)==ty)),
            'majority_train_accuracy':float(np.mean(ty==collections.Counter(training[2]).most_common(1)[0][0])),
            'cross_entropy':model.loss(tx,ty),'untrained_cross_entropy':initial.loss(tx,ty),
            'roundtrips_verified':len(packets),'codec_counts':dict(collections.Counter(CODECS[HEADER.unpack(p[:HEADER.size])[1]] for p in packets))},
        'duration_seconds':time.perf_counter()-start,
        'limitations':['Results apply to this benchmark dataset; not evidence of general intelligence or universal compression gains.',
            'Network selects standard codecs; it does not invent a new entropy coder.',
            'All archive/header bytes are counted. Model storage is separate and not required for decompression.',
            'Checkpoint promotion uses validation bytes/loss only; test improvements are not guaranteed.']}
    write_json(model_path,model.dump());report['model_bytes']=model_path.stat().st_size
    write_json(report_path,report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    benchmark=sub.add_parser('benchmark');benchmark.add_argument('--state',default='logs/learning-demo')
    benchmark.add_argument('--epochs',type=int,default=250)
    benchmark.add_argument('--corpus',help='Optional directory containing at least 20 distinct files; never uploaded')
    for name in ['compress','decompress']:
        command=sub.add_parser(name);command.add_argument('input');command.add_argument('output')
        if name=='compress':command.add_argument('--model',required=True)
    args=parser.parse_args()
    if args.command=='benchmark':
        if not 1<=args.epochs<=2000:parser.error('epochs must be 1–2000')
        report=run_benchmark(args.state,epochs=args.epochs,corpus_path=args.corpus);print(json.dumps(report['test'],indent=2));return
    with Path(args.input).open('rb') as stream:data=stream.read(MAX_BYTES+1024*1024+1)
    if len(data)>MAX_BYTES+1024*1024:raise ValueError('Archive/input too large')
    output=compress(data,NeuralSelector.load(args.model)) if args.command=='compress' else decode(data)
    with Path(args.output).open('xb') as stream:stream.write(output)


if __name__=='__main__':main()
