import random
import unittest

import support  # noqa: F401  (sets up sys.path and the rapidfuzz stand-in)

from price_compare.matcher import match_products, parse_pack_size, price_per_unit


def product(brand, simple_name, price, quantity, name=None):
    return {
        "brand": brand,
        "simple_name": simple_name,
        "name": name or f"{brand} {simple_name} {quantity}",
        "price": price,
        "quantity": quantity,
    }


class ParsePackSizeTests(unittest.TestCase):
    def test_reads_common_formats(self):
        self.assertEqual(parse_pack_size("100 g"), (100.0, "mass"))
        self.assertEqual(parse_pack_size("3 x 75 g"), (225.0, "mass"))
        self.assertEqual(parse_pack_size("1 kg"), (1000.0, "mass"))
        self.assertEqual(parse_pack_size("500 ml"), (500.0, "volume"))
        self.assertEqual(parse_pack_size("1.5 L"), (1500.0, "volume"))
        self.assertEqual(parse_pack_size("2 litre"), (2000.0, "volume"))
        self.assertEqual(parse_pack_size("6 pcs"), (6.0, "count"))
        self.assertEqual(parse_pack_size("Pack of 4"), (4.0, "count"))

    def test_unreadable_sizes_are_none(self):
        self.assertIsNone(parse_pack_size(None))
        self.assertIsNone(parse_pack_size(""))
        self.assertIsNone(parse_pack_size("family pack"))

    def test_millilitres_are_not_read_as_litres(self):
        self.assertEqual(parse_pack_size("200 ml")[0], 200.0)


class PricePerUnitTests(unittest.TestCase):
    def test_per_100_g_and_ml(self):
        self.assertEqual(price_per_unit(product("A", "x", 50, "250 g")), (20.0, "100 g"))
        self.assertEqual(price_per_unit(product("A", "x", 60, "1 L")), (6.0, "100 ml"))

    def test_unknown_size_gives_none(self):
        self.assertIsNone(price_per_unit(product("A", "x", 50, "large")))


class MatchingTests(unittest.TestCase):
    def test_same_product_is_matched_and_cheaper_platform_found(self):
        blinkit = [product("Dove", "soap bar, cream beauty", 52, "100 g")]
        zepto = [product("Dove", "soap bar, cream beauty", 48, "100 g")]
        matched, blinkit_only, zepto_only = match_products(blinkit, zepto)

        self.assertEqual(len(matched), 1)
        self.assertEqual((blinkit_only, zepto_only), ([], []))
        pair = matched[0]
        self.assertEqual(pair["cheaper_platform"], "zepto")
        self.assertEqual(pair["price_diff"], 4.0)
        self.assertEqual(pair["price_diff_percent"], 7.7)
        self.assertEqual(pair["unit_price"], {"blinkit": 52.0, "zepto": 48.0, "unit": "100 g"})

    def test_different_products_same_brand_size_and_price_are_not_matched(self):
        # The old scoring let size + price outvote a completely different name.
        blinkit = [product("Dove", "soap bar, cream beauty", 50, "100 g")]
        zepto = [product("Dove", "hair shampoo", 52, "100 g")]
        matched, blinkit_only, zepto_only = match_products(blinkit, zepto)

        self.assertEqual(matched, [])
        self.assertEqual(blinkit_only[0]["closest"]["reason"], "product name too different")

    def test_different_pack_size_is_not_matched(self):
        blinkit = [product("Dove", "soap bar, cream beauty", 50, "100 g")]
        zepto = [product("Dove", "soap bar, cream beauty", 60, "125 g")]
        matched, blinkit_only, _ = match_products(blinkit, zepto)

        self.assertEqual(matched, [])
        self.assertEqual(blinkit_only[0]["closest"]["reason"], "different pack size")

    def test_different_brand_is_not_matched(self):
        blinkit = [product("Dove", "soap bar", 50, "100 g")]
        zepto = [product("Lux", "soap bar", 50, "100 g")]
        matched, blinkit_only, _ = match_products(blinkit, zepto)

        self.assertEqual(matched, [])
        self.assertEqual(blinkit_only[0]["closest"]["reason"], "different brand")

    def test_large_price_gap_does_not_prevent_a_match(self):
        # Big gaps are the best deals; price must not be a match signal.
        blinkit = [product("Amul", "butter, salted", 60, "100 g")]
        zepto = [product("Amul", "butter, salted", 95, "100 g")]
        matched, _, _ = match_products(blinkit, zepto)

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["cheaper_platform"], "blinkit")
        self.assertEqual(matched[0]["price_diff_percent"], 36.8)

    def test_absurd_price_gap_is_rejected(self):
        blinkit = [product("Amul", "butter, salted", 60, "100 g")]
        zepto = [product("Amul", "butter, salted", 600, "100 g")]
        matched, blinkit_only, _ = match_products(blinkit, zepto)

        self.assertEqual(matched, [])
        self.assertIn("price gap", blinkit_only[0]["closest"]["reason"])

    def test_equal_prices_are_a_tie(self):
        blinkit = [product("Amul", "butter, salted", 60, "100 g")]
        zepto = [product("Amul", "butter, salted", 60, "100 g")]
        matched, _, _ = match_products(blinkit, zepto)

        self.assertEqual(matched[0]["cheaper_platform"], "tie")
        self.assertEqual(matched[0]["price_diff"], 0.0)

    def test_best_scoring_pair_wins_regardless_of_list_order(self):
        close = product("Aashirvaad", "whole wheat flour", 55, "1 kg")
        looser = product("Aashirvaad", "whole wheat flour, multigrain", 56, "1 kg")
        zepto = [product("Aashirvaad", "whole wheat flour", 52, "1 kg")]

        for blinkit in ([looser, close], [close, looser]):
            matched, blinkit_only, _ = match_products(blinkit, zepto)
            self.assertEqual(len(matched), 1)
            self.assertEqual(matched[0]["blinkit"]["simple_name"], "whole wheat flour")
            self.assertEqual(blinkit_only[0]["simple_name"], "whole wheat flour, multigrain")

    def test_result_does_not_depend_on_input_order(self):
        names = ["basmati rice", "gram flour", "yogurt, plain", "soap bar", "cooking oil, sunflower"]
        blinkit = [product("Brand", n, 100 + i * 7, "1 kg") for i, n in enumerate(names)]
        zepto = [product("Brand", n, 96 + i * 9, "1 kg") for i, n in enumerate(names)]

        def pairing(b_items, z_items):
            matched, _, _ = match_products(b_items, z_items)
            return sorted((p["blinkit"]["simple_name"], p["zepto"]["simple_name"]) for p in matched)

        expected = pairing(blinkit, zepto)
        self.assertEqual(len(expected), len(names))
        rng = random.Random(7)
        for _ in range(10):
            shuffled_b, shuffled_z = blinkit[:], zepto[:]
            rng.shuffle(shuffled_b)
            rng.shuffle(shuffled_z)
            self.assertEqual(pairing(shuffled_b, shuffled_z), expected)

    def test_missing_brand_does_not_block_a_match(self):
        blinkit = [product("", "gram flour", 40, "500 g")]
        zepto = [product("Tata Sampann", "gram flour", 42, "500 g")]
        matched, _, _ = match_products(blinkit, zepto)
        self.assertEqual(len(matched), 1)

    def test_unknown_size_falls_back_to_name_only(self):
        blinkit = [product("Dove", "soap bar, cream beauty", 50, None, name="Dove Cream Beauty Bar")]
        zepto = [product("Dove", "soap bar, cream beauty", 48, None, name="Dove Cream Beauty Bathing Bar")]
        matched, _, _ = match_products(blinkit, zepto)
        self.assertEqual(len(matched), 1)
        self.assertIsNone(matched[0]["breakdown"]["size"])

    def test_empty_inputs(self):
        self.assertEqual(match_products([], []), ([], [], []))
        matched, blinkit_only, zepto_only = match_products([product("A", "x y", 10, "1 kg")], [])
        self.assertEqual((matched, zepto_only), ([], []))
        self.assertIsNone(blinkit_only[0]["closest"])


if __name__ == "__main__":
    unittest.main()
