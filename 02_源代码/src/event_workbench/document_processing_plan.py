"""Local resource planning and parent-bound coverage; no transport or encoder."""
import json
import re
from event_workbench import compact_model_io as compact
from event_workbench.document_pipeline import validate, digest

VERSION = 'document-processing-plan-v1'
DEFAULT = dict(full_text_bytes=8000, full_units=120, block_text_bytes=4000,
               block_units=30, context_bytes=800)


def policy(value=None):
    result = dict(DEFAULT, **(value or {}))
    if set(result) != set(DEFAULT) or any(type(v) is not int or v < 1 for v in result.values()):
        raise ValueError('invalid_planning_limits')
    if result['block_text_bytes'] < 4:
        raise ValueError('block_must_fit_one_unicode_character')
    return result


def byte_size(text):
    return len(text.encode('utf-8'))


def prefix_end(text, start, end, limit):
    used = 0
    for i in range(start, end):
        used += byte_size(text[i])
        if used > limit:
            return i
    return end


def units_in(units, start, end):
    return sum(u['start'] < end and u['end'] > start for u in units)


def plan_document(document, limits=None, *, document_max_bytes=16000):
    doc = validate(document, max_bytes=document_max_bytes)
    limits = policy(limits)
    parent = digest(doc)
    _, positions = compact.source_representation(doc)
    units = [u for u in positions if u['section'] == 'body']
    text = doc['text'] or ''
    paragraphs, cursor = [], 0
    for line in text.splitlines(keepends=True):
        paragraphs.append(dict(start=cursor, end=cursor+len(line)))
        cursor += len(line)
    assert cursor == len(text)
    route = 'full_document' if byte_size(text) <= limits['full_text_bytes'] and len(units) <= limits['full_units'] else 'chunk_candidate'
    spans = []
    if route == 'full_document':
        spans = [dict(start=0, end=len(text), split_paragraph=False, forced_character_cut=False)]
    else:
        atoms = []
        for p in paragraphs:
            start = p['start']
            while start < p['end']:
                end = prefix_end(text, start, p['end'], limits['block_text_bytes'])
                fits = [u['end'] for u in units if start < u['end'] <= end and u['end'] <= p['end'] and units_in(units, start, u['end']) <= limits['block_units']]
                whole = end == p['end'] and units_in(units, start, end) <= limits['block_units']
                if not whole and fits:
                    end = max(fits)
                forced = not whole and not fits
                if end <= start:
                    raise ValueError('no_progress')
                atoms.append(dict(start=start, end=end, split_paragraph=start != p['start'] or end != p['end'], forced_character_cut=forced))
                start = end
        for atom in atoms:
            if spans and byte_size(text[spans[-1]['start']:atom['end']]) <= limits['block_text_bytes'] and units_in(units, spans[-1]['start'], atom['end']) <= limits['block_units']:
                spans[-1].update(end=atom['end'], split_paragraph=spans[-1]['split_paragraph'] or atom['split_paragraph'], forced_character_cut=spans[-1]['forced_character_cut'] or atom['forced_character_cut'])
            else:
                spans.append(dict(atom))
    headings = [p for p in paragraphs if 0 < len(text[p['start']:p['end']].strip()) <= 48 and not re.search(r'[。！？.!?；;：:]$', text[p['start']:p['end']].strip())]
    blocks = []
    for i, span in enumerate(spans):
        start, end = span['start'], span['end']
        context = []
        budget = limits['context_bytes']
        heading = next((p for p in reversed(headings) if p['end'] <= start), None)
        if heading and byte_size(text[heading['start']:heading['end']]) <= budget:
            context.append(dict(heading, purpose='preceding_short_line_heading_candidate'))
            budget -= byte_size(text[heading['start']:heading['end']])
        previous = next((u for u in reversed(units) if u['end'] <= start), None)
        clipped = False
        if previous and not any(p['start'] <= previous['start'] and previous['end'] <= p['end'] for p in context):
            a, z = previous['start'], previous['end']
            while a < z and byte_size(text[a:z]) > budget:
                a += 1
                clipped = True
            if a < z and not any(a < p['end'] and z > p['start'] for p in context):
                context.append(dict(start=a, end=z, purpose='previous_source_unit_suffix' if clipped else 'previous_source_unit'))
        context.sort(key=lambda p:p['start'])
        for j, p in enumerate(context):
            p.update(id='context-'+str(j), role='context_only', text=text[p['start']:p['end']], bytes=byte_size(text[p['start']:p['end']]))
        flags = []
        if route == 'chunk_candidate':
            flags.append('character_coverage_not_semantic_completeness; later_context_not_supplied')
        if span['split_paragraph']:
            flags.append('oversized_paragraph_split_at_available_boundary')
        if span['forced_character_cut']:
            flags.append('no_fitting_sentence_boundary; character_cut_requires_context_review')
        if clipped:
            flags.append('preceding_context_exceeds_budget; suffix_only')
        if re.search(r'该公司|上述|此前|其|this|these|the company', text[start:min(end,start+100)], re.I):
            flags.append('possible_reference_to_earlier_context')
        core = dict(id='core', role='core', field='text', start=start, end=end, text=text[start:end], bytes=byte_size(text[start:end]), units=units_in(units,start,end))
        block = dict(index=i, parent_document_id=parent, core=core, context=context,
                     metadata=dict(title=doc['title'],source=doc['source'],publication_time=doc['publication_time'],role='parent_metadata_context_only'),
                     limitations=flags, status='planned', reason='未授权分块发送；未读取密钥或占用请求')
        block['id'] = digest(dict(parent=parent,limits=limits,core=[start,end],context=context,version=VERSION))
        block['input_binding'] = digest(block)
        blocks.append(block)
    result = dict(version=VERSION,parent_document_id=parent,document=doc,limits=limits,route=route,
                  body_chars=len(text),body_bytes=byte_size(text),body_units=len(units),total_source_units=len(positions),
                  metadata_bytes=byte_size(json.dumps({k:doc[k] for k in ('title','source','publication_time')},ensure_ascii=False)),paragraphs=len(paragraphs),blocks=blocks,
                  coverage_definition='Python Unicode character offsets [start,end); text core only; title/source/time retained as parent metadata, not repeated processing',
                  semantic_completeness='not_established',next_stages='核查/关系/影响仍各受独立请求上限约束；本单未接通分块发送')
    result['coverage_proof'] = audit_plan(result)
    result['id'] = digest(result)
    return result


def audit_plan(plan):
    text = plan['document']['text'] or ''
    cursor = 0
    for b in plan['blocks']:
        core = b['core']
        if core['start'] != cursor or text[core['start']:core['end']] != core['text']:
            raise ValueError('core_gap_overlap_or_content_change')
        cursor = core['end']
        if cursor < core['start'] or cursor > len(text):
            raise ValueError('core_out_of_bounds')
        for p in b['context']:
            if p['role'] != 'context_only' or not 0 <= p['start'] < p['end'] <= core['start'] or text[p['start']:p['end']] != p['text']:
                raise ValueError('invalid_context_mapping')
    if cursor != len(text) or ''.join(b['core']['text'] for b in plan['blocks']) != text:
        raise ValueError('incomplete_core')
    return dict(core_exact=True,core_chars=cursor,core_bytes=byte_size(text),core_overlap_chars=0,
                context_chars=sum(len(p['text']) for b in plan['blocks'] for p in b['context']),
                context_bytes=sum(p['bytes'] for b in plan['blocks'] for p in b['context']),
                original_document_sha256=digest(plan['document']),single_source=True)
