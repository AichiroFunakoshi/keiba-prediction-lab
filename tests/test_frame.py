import unittest

from keiba_prediction_lab.frame import jra_frame_number


class JraFrameTest(unittest.TestCase):
    def test_official_allocations_for_common_field_sizes(self) -> None:
        self.assertEqual([jra_frame_number(i, 8) for i in range(1, 9)], list(range(1, 9)))
        self.assertEqual([jra_frame_number(i, 16) for i in range(1, 17)], [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8])
        self.assertEqual([jra_frame_number(i, 17) for i in range(13, 18)], [7, 7, 8, 8, 8])
        self.assertEqual([jra_frame_number(i, 18) for i in range(13, 19)], [7, 7, 7, 8, 8, 8])

    def test_rejects_invalid_field_or_horse_number(self) -> None:
        with self.assertRaises(ValueError):
            jra_frame_number(0, 18)
        with self.assertRaises(ValueError):
            jra_frame_number(3, 2)
