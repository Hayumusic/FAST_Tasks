import csv
import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import app as tracker
from openpyxl import load_workbook


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(tracker, "DB_PATH", Path(self.directory.name) / "test.sqlite3")
        self.path_patch.start()
        tracker.app.config.update(TESTING=True, SECRET_KEY="registry-test-key", SESSION_COOKIE_SECURE=False)
        self.client = tracker.app.test_client()
        with tracker.app.app_context():
            tracker.init_db()
            db = tracker.get_db()
            self.node_id = db.execute("INSERT INTO nodes (name, type) VALUES ('Support', 'process')").lastrowid
            self.pillar_id = db.execute("INSERT INTO pillars (name) VALUES ('Efficiency')").lastrowid
            db.commit()
        self.data = dict(node_id=self.node_id, title="Mail triage", problem_statement="Manual sorting",
                         short_description="Classify incoming mail", contributor="Haythi", status="Planned",
                         use_case_type="Automation", effort_value=30, effort_unit="Minutes",
                         frequency_per_month=20, savings_percent=80, app_tags="SAP, SAP, Workday",
                         output_type="GPT", tool_url="https://example.com/tool")

    def tearDown(self):
        self.path_patch.stop()
        self.directory.cleanup()

    def cases(self):
        return self.client.get('/api/bootstrap').get_json()['useCases']

    def test_minutes_and_hours_only_savings(self):
        response = self.client.post('/api/use-cases', json=self.data)
        self.assertEqual(response.status_code, 200)
        case = self.cases()[0]
        self.assertEqual(case['effort_hours'], .5)
        self.assertEqual(case['monthly_hours_saved'], 8)
        self.assertFalse(case['cost_provided'])
        self.assertEqual(case['app_tags'], 'SAP, Workday')

    def test_cost_pillar_edit_and_zero_cost(self):
        payload = dict(self.data, cost_per_run=100, pillar_id=self.pillar_id)
        case_id = self.client.post('/api/use-cases', json=payload).get_json()['id']
        case = self.cases()[0]
        self.assertEqual(case['estimated_monthly_savings'], 1600)
        self.assertEqual(case['pillar'], 'Efficiency')
        self.assertEqual(len(self.client.get('/api/bootstrap?pillar=Efficiency').get_json()['useCases']), 1)
        self.assertEqual(self.client.put(f'/api/use-cases/{case_id}', json=dict(payload, cost_per_run=0)).status_code, 200)
        self.assertTrue(self.cases()[0]['cost_provided'])
        self.client.put(f'/api/use-cases/{case_id}', json=dict(payload, cost_per_run=''))
        self.assertFalse(self.cases()[0]['cost_provided'])

    def test_invalid_input_and_links(self):
        for fields in [dict(effort_value=-1), dict(savings_percent=101), dict(effort_value='nan'),
                       dict(tool_url='javascript:alert(1)'), dict(node_id=999), dict(title='')]:
            self.assertEqual(self.client.post('/api/use-cases', json=dict(self.data, **fields)).status_code, 400)
        self.assertEqual(self.cases(), [])

    def upload(self, records, preview=False):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=['Node ID', 'Use Case Title', 'Current Problem / Opportunity',
                                'Short Description', 'Contributor', 'Type', 'Status', 'Effort Per Run', 'Effort Unit',
                                'Frequency / Month', 'Savings From Automation %'])
        writer.writeheader()
        writer.writerows(records)
        data = {'file': (io.BytesIO(stream.getvalue().encode()), 'cases.csv')}
        if preview:
            data['preview'] = 'true'
        return self.client.post('/api/use-cases/import', data=data)

    def test_bulk_preview_atomic_validation_and_append(self):
        record = {'Node ID': self.node_id, 'Use Case Title': 'Bulk example',
                  'Current Problem / Opportunity': 'Manual', 'Short Description': 'Automation',
                  'Contributor': 'Nathan', 'Type': 'AI', 'Status': 'Built', 'Effort Per Run': 15,
                  'Effort Unit': 'Minutes', 'Frequency / Month': 40, 'Savings From Automation %': 50}
        self.assertEqual(self.upload([record], preview=True).get_json()['count'], 1)
        self.assertEqual(self.cases(), [])
        failed = self.upload([record, dict(record, **{'Node ID': 999})])
        self.assertEqual(failed.status_code, 400)
        self.assertIn('Row 3', failed.get_json()['errors'][0])
        self.assertEqual(self.cases(), [])
        self.assertEqual(self.upload([record]).get_json()['imported'], 1)
        self.assertEqual(self.cases()[0]['monthly_hours_saved'], 5)
        self.assertEqual(self.client.get('/api/bootstrap').get_json()['contributors'], ['Nathan'])

    def test_template_export_and_roundtrip(self):
        self.client.post('/api/use-cases', json=self.data)
        template = load_workbook(io.BytesIO(self.client.get('/api/use-cases/template').data))
        self.assertIn('Hierarchy Reference', template.sheetnames)
        exported = self.client.get('/export').data
        book = load_workbook(io.BytesIO(exported))
        self.assertIn('Monthly Hours Saved', [c.value for c in book.active[1]])
        result = self.client.post('/api/use-cases/import', data={'file': (io.BytesIO(exported), 'export.xlsx')})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.cases()), 2)
        self.assertEqual(self.cases()[0]['monthly_hours_saved'], 8)
        self.assertFalse(self.cases()[0]['cost_provided'])

    def test_setup_still_admin_only(self):
        self.assertEqual(self.client.post('/api/nodes', json={'name': 'No', 'type': 'process'}).status_code, 401)
        self.assertEqual(self.client.post('/api/use-cases', json=self.data).status_code, 200)

    def test_migrate_old_schema(self):
        legacy_path = Path(self.directory.name) / 'legacy.sqlite3'
        with closing(sqlite3.connect(legacy_path)) as db:
            db.execute('CREATE TABLE use_cases (id INTEGER PRIMARY KEY, node_id INTEGER, title TEXT, problem_statement TEXT, use_case_type TEXT, short_description TEXT, status TEXT, contributor TEXT, created_date TEXT, updated_date TEXT, effort_hours REAL, cost_per_run REAL, frequency_per_month REAL, savings_percent REAL)')
            db.execute("INSERT INTO use_cases VALUES (1, 1, 'Legacy', 'Pain', 'AI', 'Description', 'Built', 'Person', '2026-01-01', '2026-01-01', 2, 100, 10, 50)")
            db.commit()
        with patch.object(tracker, 'DB_PATH', legacy_path), tracker.app.app_context():
            tracker.init_db()
            db = tracker.get_db()
            db.execute("INSERT INTO nodes (id, name, type) VALUES (1, 'Legacy process', 'process')")
            db.commit()
            with tracker.app.test_request_context('/'):
                case = tracker.filtered_use_cases()[0]
                self.assertEqual(case['monthly_hours_saved'], 10)
                self.assertTrue(case['cost_provided'])
                self.assertEqual(case['effort_unit'], 'Hours')


if __name__ == '__main__':
    unittest.main()
