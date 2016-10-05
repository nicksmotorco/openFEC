import re
import subprocess
from mock import patch

import pytest

import manage
from webservices import rest
from webservices.load_current_murs import parse_regulatory_citations, parse_statutory_citations
from tests.common import TEST_CONN, BaseTestCase

@pytest.mark.parametrize("test_input,case_id,entity_id,expected", [
    ("110", 1, 2,
        ["/regulations/110/CURRENT"]),
    ("110.21", 1, 2,
        ["/regulations/110-21/CURRENT"]),
])
def test_parse_regulatory_citations(test_input, case_id, entity_id, expected):
    assert parse_regulatory_citations(test_input, case_id, entity_id) == expected

def test_parse_statutory_citations_with_reclassifications():
    assert parse_statutory_citations("431", 1, 2) == [
        "https://api.fdsys.gov/link?collection=uscode&year=mostrecent&link-type=html&title=52&section=30101"]

def test_parse_statutory_citations_no_reclassifications():
    assert parse_statutory_citations("30101", 1, 2) == [
        "https://api.fdsys.gov/link?collection=uscode&year=mostrecent&link-type=html&title=52&section=30101"]

def assert_es_index_call(call_args, expected_mur):
    index, doc_type, mur = call_args[0]
    assert index == 'docs'
    assert doc_type == 'murs'
    assert mur == expected_mur

class TestLoadCurrentMURs(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super(TestLoadCurrentMURs, cls).setUpClass()
        subprocess.check_call(
            ['psql', TEST_CONN, '-f', 'data/load_murs_schema.sql'])

    @classmethod
    def tearDownClass(cls):
        subprocess.check_call(
            ['psql', TEST_CONN, '-c', 'DROP SCHEMA fecmur CASCADE'])
        super(TestLoadCurrentMURs, cls).tearDownClass()

    def setUp(self):
        self.connection = rest.db.engine.connect()

    def tearDown(self):
        self.clear_test_data()
        self.connection.close()
        rest.db.session.remove()

    @patch('webservices.load_current_murs.get_bucket')
    @patch('webservices.load_current_murs.get_elasticsearch_connection')
    def test_simple_mur(self, get_es_conn, get_bucket):
        mur_subject = 'Fraudulent misrepresentation'
        expected_mur = {
            'no': '1',
            'name': 'Simple MUR',
            'mur_type': 'current',
            'text': '',
            'doc_id': 'mur_1',
            'participants': [],
            'subject': {"text": [mur_subject]},
            'documents': []
        }
        self.create_mur(1, expected_mur['no'], expected_mur['name'], mur_subject)
        manage.load_current_murs()
        index, doc_type, mur = get_es_conn.return_value.index.call_args[0]

        assert index == 'docs'
        assert doc_type == 'murs'
        assert mur == expected_mur

    @patch('webservices.env.env.get_credential', return_value='BUCKET_NAME')
    @patch('webservices.load_current_murs.get_bucket')
    @patch('webservices.load_current_murs.get_elasticsearch_connection')
    def test_mur_with_participants_and_documents(self, get_es_conn, get_bucket, get_credential):
        case_id = 1
        mur_subject = 'Fraudulent misrepresentation'
        expected_mur = {
            'no': '1',
            'name': 'MUR with participants',
            'mur_type': 'current',
            'doc_id': 'mur_1',
            'subject': {"text": [mur_subject]},
        }
        participants = [
            ("Complainant", "Gollum"),
            ("Respondent", "Bilbo Baggins"),
            ("Respondent", "Thorin Oakenshield")
        ]
        documents = [
            ('A Category', 'Some text'),
            ('Another Category', 'Different text'),
        ]

        self.create_mur(case_id, expected_mur['no'], expected_mur['name'], mur_subject)
        for entity_id, participant in enumerate(participants):
            role, name = participant
            self.create_participant(case_id, entity_id, role, name)
        for document_id, document in enumerate(documents):
            category, ocrtext = document
            self.create_document(case_id, document_id, category, ocrtext)

        manage.load_current_murs()
        index, doc_type, mur = get_es_conn.return_value.index.call_args[0]

        assert index == 'docs'
        assert doc_type == 'murs'
        for key in expected_mur:
            assert mur[key] == expected_mur[key]

        assert participants == [(p['role'], p['name'])
                                for p in mur['participants']]

        assert mur['text'].strip() == "Some text Different text"

        assert [(d[0], len(d[1])) for d in documents] == [
            (d['category'], d['length']) for d in mur['documents']]
        for d in mur['documents']:
            assert re.match(r'https://BUCKET_NAME.s3.amazonaws.com/legal/murs/current', d['url'])

    @patch('webservices.env.env.get_credential', return_value='BUCKET_NAME')
    @patch('webservices.load_current_murs.get_bucket')
    @patch('webservices.load_current_murs.get_elasticsearch_connection')
    def test_mur_with_citations(self, get_es_conn, get_bucket, get_credential):
        case_id = 1
        mur_subject = 'Fraudulent misrepresentation'
        expected_mur = {
            'no': '1',
            'name': 'MUR with participants',
            'mur_type': 'current',
            'doc_id': 'mur_1',
            'subject': {"text": [mur_subject]},
        }
        participants = [
            ("Complainant", "Gollum"),
            ("Respondent", "Bilbo Baggins", "RTB", "345", ""),
            ("Respondent", "Thorin Oakenshield", "Closed", "123", "456")
        ]

        self.create_mur(case_id, expected_mur['no'], expected_mur['name'], mur_subject)
        for entity_id, participant in enumerate(participants):
            if len(participant) == 5:
                role, name, stage, statutory_citation, regulatory_citation = participant
                self.create_participant(case_id, entity_id, role, name, stage, statutory_citation, regulatory_citation)
            else:
                role, name = participant
                self.create_participant(case_id, entity_id, role, name)

        manage.load_current_murs()
        index, doc_type, mur = get_es_conn.return_value.index.call_args[0]

        assert index == 'docs'
        assert doc_type == 'murs'
        for key in expected_mur:
            assert mur[key] == expected_mur[key]

        gollum = [p for p in mur['participants']
                  if p['name'] == 'Gollum'][0]
        assert gollum['role'] == 'Complainant'

        bilbo = [p for p in mur['participants']
                 if p['name'] == 'Bilbo Baggins'][0]
        assert bilbo['role'] == 'Respondent'
        assert len(bilbo['citations']['RTB']) == 1
        assert re.search(r'api.fdsys.gov.*collection=uscode.*section=345', bilbo['citations']['RTB'][0])

        thorin = [p for p in mur['participants']
                 if p['name'] == 'Thorin Oakenshield'][0]
        assert thorin['role'] == 'Respondent'
        assert len(thorin['citations']['Closed']) == 2
        assert re.search(r'api.fdsys.gov.*collection=uscode.*section=123', thorin['citations']['Closed'][0])
        assert thorin['citations']['Closed'][1] == '/regulations/456/CURRENT'

    def create_mur(self, case_id, case_no, name, subject_description):
        subject_id = self.connection.execute(
            "SELECT subject_id FROM fecmur.subject "
            " WHERE description = %s ", subject_description).scalar()
        self.connection.execute(
            "INSERT INTO fecmur.case (case_id, case_no, name, case_type) "
            "VALUES (%s, %s, %s, 'MUR')", case_id, case_no, name)
        self.connection.execute(
            "INSERT INTO fecmur.case_subject (case_id, subject_id, relatedsubject_id) "
            "VALUES (%s, %s, -1)", case_id, subject_id)

    def create_participant(self, case_id, entity_id, role, name,
            stage=None, statutory_citation=None, regulatory_citation=None):
        role_id = self.connection.execute(
            "SELECT role_id FROM fecmur.role "
            " WHERE description = %s ", role).scalar()
        self.connection.execute(
            "INSERT INTO fecmur.entity (entity_id, name) "
            "VALUES (%s, %s)", entity_id, name)
        self.connection.execute(
            "INSERT INTO fecmur.players (player_id, entity_id, case_id, role_id) "
            "VALUES (%s, %s, %s, %s)", entity_id, entity_id, case_id, role_id)
        if stage:
            self.create_citation(case_id, entity_id, stage, statutory_citation, regulatory_citation)

    def create_citation(self, case_id, entity_id, stage, statutory_citation, regulatory_citation):
        self.connection.execute(
            "INSERT INTO fecmur.violations (case_id, entity_id, stage, statutory_citation, regulatory_citation) "
            "VALUES (%s, %s, %s, %s, %s)", case_id, entity_id, stage, statutory_citation, regulatory_citation)

    def create_document(self, case_id, document_id, category, ocrtext):
        self.connection.execute(
            "INSERT INTO fecmur.document (document_id, doc_order_id, case_id, category, ocrtext, fileimage) "
            "VALUES (%s, %s, %s, %s, %s, %s)", document_id, document_id, case_id, category, ocrtext, ocrtext)

    def clear_test_data(self):
        tables = [
            "violations",
            "document",
            "players",
            "entity",
            "case_subject",
            "case",
        ]
        for table in tables:
            self.connection.execute("DELETE FROM fecmur.{}".format(table))
