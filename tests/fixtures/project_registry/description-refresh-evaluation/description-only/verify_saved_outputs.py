"""Offline replay of all saved real model outputs through the normal codec."""
import copy
import json
import tempfile
from pathlib import Path
from project_registry_core import database, editorial, project_record, registry

OUT = Path(__file__).resolve().parent
cases = {c['id']: c for name in ('inputs.json',)
         for c in json.loads((OUT / name).read_text())}
attempts = json.loads((OUT / 'attempts.json').read_text())
assert len(attempts) == 6 and all(a['status'] == 'complete' for a in attempts)
ROOT = next(p for p in OUT.parents if (p / 'bin/project-registry-test').is_file())
TMP = ROOT / '.tmp'
TMP.mkdir(exist_ok=True)
work = tempfile.TemporaryDirectory(prefix='description-replay-', dir=TMP)
conn = database.open_database(Path(work.name) / 'offline-replay.db', create=True)
count = 0
for a in attempts:
    result = json.loads((OUT / (a['key'] + '.json')).read_text())
    for protection, expected in result['applied'].items():
        p = copy.deepcopy(cases[result['case']]['project'])
        p['project_key'] += '-' + a['key'] + '-' + protection
        if protection.startswith('unknown'):
            p.pop('editorial', None)
        if protection == 'unknown_matching_prior_output':
            p['llm_output'] = editorial.profile(p)
        if protection == 'operator':
            editorial.record_edit(p, allow_automatic=False, updated_at='2026-09-22T00:00:00+00:00')
        if protection == 'modified_after_automatic':
            p['editorial']['profile_hash'] = 'previous-different-profile-hash'
        payload = result['output']
        proposed = {'tagline': registry.normalize_tagline(payload['tagline']),
            'summary': project_record.normalize_ja_en_mixed_spacing(payload['summary'].strip()),
            'features': registry.clean_human_string_list(payload['features'], limit=6)}
        editorial.apply_proposal(p, proposed, payload['description_change'],
            result['evidence']['description_sources'], generated_at=result['meta']['generated_at'])
        project_record.save_project(conn, p)
        conn.commit()
        loaded = project_record.fetch_project(conn, p['project_key'])
        assert editorial.profile(loaded) == expected['after'], (a['key'], protection)
        assert loaded['editorial'] == expected['editorial'], (a['key'], protection)
        assert loaded['tracking_state'] == expected['tracking_state'] == 'stable'
        if protection != 'automatic':
            assert not editorial.automatic(loaded)
            assert editorial.profile(loaded) == expected['before']
        count += 1
assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
assert not conn.execute('PRAGMA foreign_key_check').fetchall()
conn.close()
work.cleanup()
print(f'{count} saved-output/protection combinations reproduced; integrity and foreign keys OK; no network.')
