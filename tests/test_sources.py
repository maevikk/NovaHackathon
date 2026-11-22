from pathlib import Path
from csv import DictReader
import unittest,json
from xml.etree import ElementTree as ET
ROOT=Path(__file__).parents[1]
class SourceTests(unittest.TestCase):
 def test_normative_sum_csv(self):
  with (ROOT/'analysis/data/source_reconciliation.csv').open('r',encoding='utf8') as fp:
   rows=list(DictReader(fp))
  self.assertEqual(len(rows),10)
  for row in rows:
   self.assertEqual(sum(map(int,row['steps_seconds'].split('+'))),int(row['sum_norm_seconds']))
   self.assertEqual(row['source_status'],'documented_work_standard')
   self.assertEqual(row['fact_ct_status'],'unknown_not_measured')
 def test_bpmn_xml_valid(self):
  root=ET.parse(ROOT/'diagrams/as_is.bpmn').getroot()
  self.assertEqual(root.tag.split('}')[-1],'definitions')
  self.assertEqual(len(root.findall('.//{*}sequenceFlow')),17)
 def test_svg_editable(self):
  ET.parse(ROOT/'diagrams/as_is_kpsc.svg')
  ET.parse(ROOT/'diagrams/to_be_hypothesis.svg')
 def test_schema_both_datasets(self):
  s=json.loads((ROOT/'data/schema.json').read_text('utf8'))
  self.assertEqual(s['properties']['schema_version']['const'],4)
  for f in ['venio_factory.json','generic_example.json']:
   m=json.loads((ROOT/'data'/f).read_text('utf8'))
   self.assertEqual(m['schema_version'],4)
if __name__=='__main__':unittest.main()
