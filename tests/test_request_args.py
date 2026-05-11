import unittest

from werkzeug.datastructures import MultiDict

from app.api.request_args import (
    RANKING_SEARCH_MAX_LENGTH,
    clamp_page_to_total,
    pages_for,
    positive_int_arg,
    ranking_request_args,
    search_arg,
)


class RequestArgValidationTests(unittest.TestCase):
    def test_ranking_request_args_clamps_normal_ranking_limit_to_frontend_max(self):
        args = MultiDict({
            'page': '2',
            'limit': '999',
            'search': '  ember  ',
        })

        self.assertEqual(
            ranking_request_args(args, default_limit=10, max_limit=50),
            (2, 50, 'ember'),
        )

    def test_ranking_request_args_clamps_seasonal_limit_to_frontend_max(self):
        args = MultiDict({
            'page': '1',
            'limit': '50',
        })

        self.assertEqual(
            ranking_request_args(args, default_limit=20, max_limit=20),
            (1, 20, ''),
        )

    def test_positive_int_arg_rejects_invalid_and_non_positive_values(self):
        with self.assertRaisesRegex(ValueError, 'page must be an integer'):
            positive_int_arg(MultiDict({'page': 'not-a-number'}), 'page', 1)

        with self.assertRaisesRegex(ValueError, 'page must be at least 1'):
            positive_int_arg(MultiDict({'page': '0'}), 'page', 1)

    def test_search_arg_rejects_unbounded_search_strings(self):
        args = MultiDict({'search': 'x' * (RANKING_SEARCH_MAX_LENGTH + 1)})

        with self.assertRaisesRegex(ValueError, 'search must be at most'):
            search_arg(args)

    def test_page_clamp_uses_total_count_before_offset_query(self):
        self.assertEqual(pages_for(101, 50), 3)
        self.assertEqual(clamp_page_to_total(9999, 101, 50), 3)
        self.assertEqual(clamp_page_to_total(9999, 0, 50), 1)


if __name__ == '__main__':
    unittest.main()
