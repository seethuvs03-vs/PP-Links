import json
import re
from pathlib import Path

def load_rules(path):
    config=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(config.get('fallback'),str) or not isinstance(config.get('rules'),list): raise ValueError('Categories need fallback and rules')
    for rule in config['rules']:
        if not all(isinstance(rule.get(k),str) and rule[k].strip() for k in ('category','subcategory')): raise ValueError('Each rule needs category and subcategory')
        if not isinstance(rule.get('keywords'),list) or not all(isinstance(k,str) and k.strip() for k in rule['keywords']): raise ValueError('Keywords must be nonempty strings')
    return config

def categorize(metadata, config):
    text=' '.join(str(metadata.get(k) or '') for k in ('title','caption','description','transcript','hashtags','keywords')).casefold()
    best, hits=None, []
    for rule in config['rules']:
        found=[k for k in rule['keywords'] if re.search(r'(?<!\w)'+re.escape(k.casefold())+r'(?!\w)',text)]
        if len(found)>len(hits): best,hits=rule,found
    return dict(category=best['category'] if best else config['fallback'], subcategory=best['subcategory'] if best else '', keywords=hits, confidence=None, reason='Matched: '+', '.join(hits) if hits else 'No configured keyword matched; manual review needed')
