"""Optional small-sample OCR audit. Output is quarantined and never imported."""
import argparse
import hashlib
import json
from pathlib import Path


def benchmark(image_path,reference):
    from PIL import Image,ImageOps
    import numpy as np
    from rapidocr import RapidOCR
    content=Path(image_path).read_bytes()
    if hashlib.sha256(content).hexdigest()!=reference['image_sha256']: raise ValueError('Source image changed')
    engine=RapidOCR(params={'EngineConfig.onnxruntime.intra_op_num_threads':1,'EngineConfig.onnxruntime.inter_op_num_threads':1})
    image=Image.open(image_path).convert('RGB'); results=[]
    for item in reference['cells']:
        crop=image.crop(item['box']);crop=ImageOps.grayscale(crop).resize((crop.width*3,crop.height*3))
        output=engine(np.array(crop),use_det=False,use_cls=False,use_rec=True)
        text=''.join(output.txts or ())
        results.append({**item,'ocr':text,'exact_match':text==item['expected_from_visual_transcription']})
    return {'image_sha256':reference['image_sha256'],'source_url':reference['source_url'],
        'sample_cells':len(results),'exact_matches':sum(x['exact_match'] for x in results),
        'status':'quarantined','approved_training_rows':0,
        'limitation':'Convenience sample, not a population error rate. Date/headers/rights need separate review.','cells':results}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('image',type=Path);p.add_argument('--reference',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=benchmark(a.image,json.loads(a.reference.read_text(encoding='utf-8')))
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='cells'},ensure_ascii=False))
