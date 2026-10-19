import json
from pathlib import Path
import random
import struct
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from learned_compression import CODECS,HEADER,MAX_BYTES,NeuralSelector,corpus,decode,encode,features,file_corpus,run_benchmark


class CompressionTests(unittest.TestCase):
    def test_exact_reconstruction_for_every_codec_and_byte_type(self):
        samples=[b'',b'one byte\0',bytes(range(256))*4,random.Random(9).randbytes(4096),'节点 U0001f426 λ'.encode()*200]
        for codec in CODECS:
            for data in samples:
                with self.subTest(codec=codec,length=len(data)):
                    self.assertEqual(decode(encode(data,codec)),data)
    def test_damage_truncation_and_oversized_output_are_rejected(self):
        packet=encode(b'original','raw')
        for bad in [packet[:-1],packet[:-1]+b'x',b'nope',HEADER.pack(b'XCB1',0,MAX_BYTES+1,0)]:
            with self.assertRaises(ValueError):decode(bad)
        compressed=encode(b'x'*100000,'zlib')
        bomb=HEADER.pack(b'XCB1',1,1,0)+compressed[HEADER.size:]
        with self.assertRaises(ValueError):decode(bomb)
    def test_backprop_matches_independent_numerical_derivative(self):
        model=NeuralSelector();x=np.array([features(b'abc'*100),features(bytes(range(256)))]);y=np.array([1,0])
        gradients=model.gradients(x,y)
        for name,index in [('w1',(2,3)),('w2',(4,1)),('b1',(7,)),('b2',(0,))]:
            value=model.parameters[name][index];step=1e-5
            model.parameters[name][index]=value+step;upper=model.loss(x,y)
            model.parameters[name][index]=value-step;lower=model.loss(x,y)
            model.parameters[name][index]=value
            self.assertAlmostEqual(gradients[name][index],(upper-lower)/(2*step),places=6)
    def test_model_roundtrip_and_invalid_weights(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'model.json';model=NeuralSelector();data=model.dump()
            path.write_text(json.dumps(data));restored=NeuralSelector.load(path)
            np.testing.assert_array_equal(model.predict(np.eye(10)),restored.predict(np.eye(10)))
            data['parameters']['w1'][0][0]=float('nan');path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):NeuralSelector.load(path)
    def test_training_improves_unseen_prediction_and_measures_real_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            report=run_benchmark(temp);test=report['test']
            self.assertEqual(report['parameter_count'],339)
            self.assertGreater(test['accuracy'],test['untrained_accuracy'])
            self.assertLess(test['cross_entropy'],test['untrained_cross_entropy'])
            self.assertLess(test['compressed_bytes'],test['zlib_with_raw_fallback_bytes'])
            self.assertLessEqual(test['oracle_bytes'],test['compressed_bytes'])
            self.assertEqual(test['roundtrips_verified'],60)
            self.assertEqual(len(set(report['split_sha256'])),3)
            resumed=run_benchmark(temp,epochs=10)
            self.assertEqual(resumed['generation'],2)
    def test_file_split_deduplicates_and_requires_real_holdout(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for index in range(20):(root/f'{index}.txt').write_text(f'document {index}')
            (root/'duplicate.txt').write_text('document 0')
            splits,_=file_corpus(root);self.assertEqual(sum(map(len,splits)),20)
            self.assertFalse(set(splits[0])&set(splits[2]))


if __name__=='__main__':unittest.main()
