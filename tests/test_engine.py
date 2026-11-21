from __future__ import annotations
import copy
import json
import unittest
from pathlib import Path
from backend.engine import simulate
from backend.analytics import analyze, compare
from backend.validation import ModelError, validate_model

MODEL = json.loads((Path(__file__).resolve().parents[1] / 'data/factory_demo_v3.json').read_text('utf-8'))


class SimulationTests(unittest.TestCase):
    def test_baseline_matches_js_frontend(self):
        r = simulate(MODEL)
        # Контрольные значения получены реальным вызовом simulate(DEFAULT) в app.js v2,
        # а НЕ исходными результатами фабрики.
        self.assertEqual((r['accepted'], r['finished'], r['wip']), (9, 10, 24))
        self.assertEqual((r['totalScrap'], r['logistics']['trips']), (0, 68))
        self.assertAlmostEqual(r['financial']['profit'], 444026.6666666666, places=4)
        self.assertAlmostEqual(r['logistics']['distance'], 695.0858788521236, places=6)
        self.assertEqual(len(r['perOp']), 10)
        self.assertEqual(r['series'][-1]['value'], r['accepted'])

    def test_reproducible_seed(self):
        self.assertEqual(simulate(MODEL), simulate(copy.deepcopy(MODEL)))

    def test_move_on_kpsc_does_not_change_numbers(self):
        other = copy.deepcopy(MODEL)
        other['nodes'][4]['x'] += 600
        other['nodes'][4]['y'] -= 300
        a, b = simulate(MODEL), simulate(other)
        self.assertEqual(a, b)

    def test_move_on_floor_changes_distance_only_if_enabled(self):
        other = copy.deepcopy(MODEL)
        other['kpsc']['floor']['o4'] = [0.5, 0.5]
        self.assertNotEqual(simulate(MODEL)['logistics']['distance'], simulate(other)['logistics']['distance'])
        base = copy.deepcopy(MODEL)
        base['settings']['layoutLogistics'] = False
        altered = copy.deepcopy(other)
        altered['settings']['layoutLogistics'] = False
        self.assertEqual(simulate(base)['accepted'], simulate(altered)['accepted'])
        self.assertEqual(simulate(base)['logistics']['trips'], simulate(altered)['logistics']['trips'])
        # Расстояние как справочная метрика меняется, хотя время логиста выключено.

    def test_info_arrows_and_documentary_fields_not_used_as_material_dependencies(self):
        model = copy.deepcopy(MODEL)
        model['kpsc']['infoLinks'].append({'id': 'ignored', 'from': 'o9', 'to': 'o3'})
        model['kpsc']['meta']['o4']['processCost'] = 12500
        model['kpsc']['meta']['o4']['downtimeMinutes'] = 9
        self.assertEqual(simulate(MODEL), simulate(model))
        self.assertEqual(analyze(model)['standaloneOperationCapacity'][3]['processCostDocumented'], 12500)

    def test_cycle_rejected(self):
        model = copy.deepcopy(MODEL)
        model['edges'].append({'from': 'o9', 'to': 'o4'})
        with self.assertRaisesRegex(ModelError, 'Циклические'):
            validate_model(model)

    def test_missing_dependency_rejected(self):
        model = copy.deepcopy(MODEL)
        model['edges'] = [e for e in model['edges'] if not (e['from'] == 'o3' and e['to'] == 'o4')]
        # Формально o3 больше не ведёт к ОТК; модель нельзя считать.
        with self.assertRaises(ModelError):
            validate_model(model)

    def test_invalid_numbers_rejected(self):
        for key, value in [('cycle', 0), ('staff', 1.5), ('availability', 150)]:
            model = copy.deepcopy(MODEL)
            model['nodes'][1][key] = value
            with self.subTest(key=key), self.assertRaises(ModelError):
                validate_model(model)

    def test_compare_outputs_delta(self):
        other = copy.deepcopy(MODEL)
        other['nodes'][4]['cycle'] = 75
        result = compare(MODEL, other)
        self.assertEqual(result['delta']['accepted']['change'], result['candidate']['accepted'] - result['baseline']['accepted'])
        self.assertIn('criticalPathNodes', result['analysis']['candidate'])

    def test_warehouse_and_qc_constraints(self):
        model = copy.deepcopy(MODEL)
        model['edges'].append({'from': 'o1', 'to': 'customer'})
        with self.assertRaises(ModelError):
            validate_model(model)

    def test_unrealistic_stock_is_explicit(self):
        model = copy.deepcopy(MODEL)
        model['settings']['initialFlowWip'] = 0
        r = simulate(model)
        self.assertEqual(r['initialWipCount'], 0)
        self.assertTrue(0 <= r['accepted'] <= r['target'])


if __name__ == '__main__':
    unittest.main()
