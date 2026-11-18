"""User-entered details stay separate from retrieved platform metadata."""
def effective_details(metadata):
    result=dict(metadata)
    result['details_source']=metadata.get('retrieval_method') or metadata.get('source','')
    manual=metadata.get('manual_details',{})
    result.update({key:value for key,value in manual.items() if value})
    if manual:
        result['manual_fields']=list(manual)
        result['details_source']='User-entered details; platform retrieval status is separate'
    return result
