import unittest
from datetime import datetime
from test_jra_web_workflow import RESULT, RESULT_URL, _encoded
from keiba_prediction_lab.result_observation import parse_result_observation, parse_result_payouts

class ResultObservationTest(unittest.TestCase):
    def test_weather_is_explicitly_post_event(self):
        result=parse_result_observation(_encoded(RESULT),RESULT_URL,datetime.fromisoformat('2098-12-30T18:00:00+09:00'))
        self.assertEqual(result['weather'],'晴')
        self.assertEqual(result['surface_conditions'],{'芝':'良'})
        self.assertEqual(result['usage'],'post_event_diagnostic_only')
        with self.assertRaises(ValueError):parse_result_observation(_encoded(RESULT),RESULT_URL,datetime.fromisoformat('2098-12-30T10:00:00+09:00'))
        with self.assertRaises(ValueError):parse_result_observation(_encoded(RESULT.replace('天候晴','')),RESULT_URL,datetime.fromisoformat('2098-12-30T18:00:00+09:00'))

    def test_missing_payouts_fail_instead_of_inferring_from_finish(self):
        with self.assertRaises(ValueError):parse_result_payouts(_encoded(RESULT),RESULT_URL)

    def test_all_six_types_multiple_place_lines(self):
        # Add a third finisher and explicit synthetic official payouts.
        third='<tr><td class="place">3</td><td class="num">3</td><td class="horse">テストC</td><td class="weight">55</td></tr>'
        s=RESULT.replace('</tbody>',third+'</tbody>')
        mapping={'win':['1'],'place':['1','2','3'],'umaren':['1-2'],'umatan':['1-2'],'trio':['1-2-3'],'tierce':['1-2-3']}
        refund='<div class="refund_area"><ul>'
        for cls,selections in mapping.items():
            refund+=f'<li class="{cls}">' + ''.join(f'<div class="line"><div class="num">{sel}</div><div class="yen">1,230円</div></div>' for sel in selections)+'</li>'
        refund+='</ul></div>'
        p=parse_result_payouts(_encoded(s.replace('</body>',refund+'</body>')),RESULT_URL)
        self.assertEqual(len(p.payouts),8)
        self.assertTrue(all(x.payout_yen==1230 for x in p.payouts))
