import re
from datetime import datetime
from functools import cached_property
from urllib.parse import urlparse

from dateutil.relativedelta import relativedelta


class AnswerValidator:
    MAX_NUMBER = 9999999999
    MIN_NUMBER = -999999999
    MAX_DECIMAL_PLACES = 6

    DECIMAL_PLACES_UNDEFINED = "'decimal_places' must be defined and set to 2"
    DECIMAL_PLACES_TOO_LONG = "Number of decimal places is greater than system limit"
    INVALID_OFFSET_DATE = (
        "The minimum offset date is greater than the maximum offset date"
    )
    INVALID_SUGGESTION_URL = "Suggestions url is invalid"
    LIST_NAME_MISSING = "List name defined in action params does not exist"
    BLOCK_ID_MISSING = "Block id defined in action params does not exist"
    VALUE_MISMATCH = "Found mismatching answer value"
    DEFAULT_ON_MANDATORY = "Default is being used with a mandatory answer"
    MINIMUM_LESS_THAN_LIMIT = "Minimum value is less than system limit"
    MAXIMUM_GREATER_THAN_LIMIT = "Maximum value is greater than system limit"

    def __init__(self, schema_element, block=None, list_names=None, block_ids=None):
        self.answer = schema_element
        self.block = block
        self.list_names = list_names
        self.block_ids = block_ids

        self.errors = []

    @cached_property
    def options(self):
        return self.answer.get("options", [])

    def add_error(self, message, **context):
        context["id"] = self.answer["id"]

        self.errors.append({"message": message, **context})

    def validate(self):
        self._validate_duplicate_options()
        self._validate_answer_actions()
        self.validate_labels_and_values_match()

        self._validate_routing_on_answer_options()

        if not self.are_decimal_places_valid():
            self.add_error(self.DECIMAL_PLACES_UNDEFINED)

        if not self.is_offset_date_valid():
            self.add_error(self.INVALID_OFFSET_DATE)

        if (
            self.answer["type"] == "TextField"
            and "suggestions_url" in self.answer
            and not self.is_suggestion_url_valid()
        ):
            self.add_error(self.INVALID_SUGGESTION_URL)

        if self.answer["type"] in ["Number", "Currency", "Percentage"]:
            # Validate default is only used with non mandatory answers
            self.validate_numeric_default()

            # Validate numeric answer value within system limits
            self.validate_numeric_answer_value()

            # Validate numeric answer decimal places within system limits
            self.validate_numeric_answer_decimals()

    def _validate_duplicate_options(self):
        errors = []

        labels = set()
        values = set()

        for option in self.options:

            # labels can have placeholders in, in which case we won't know if they are a duplicate or not
            if isinstance(option["label"], dict):
                continue

            if option["label"] in labels:
                errors.append("Duplicate label found - {}".format(option["label"]))

            if option["value"] in values:
                errors.append("Duplicate value found - {}".format(option["value"]))

            labels.add(option["label"])
            values.add(option["value"])

        return errors

    def are_decimal_places_valid(self):
        if "calculated" in self.answer:
            return (
                "decimal_places" in self.answer and self.answer["decimal_places"] == 2
            )
        return True

    def validate_labels_and_values_match(self):
        errors = []

        for option in self.options:
            if "text_plural" in option["label"]:
                continue

            if isinstance(option["label"], str):
                label = option["label"]
            else:
                label = option["label"]["text"]

            if label != option["value"]:
                errors.append(
                    f"Found mismatching answer value for label: {label} "
                    f'in answer id: {self.answer["id"]}'
                )
        return errors

    def is_offset_date_valid(self):
        if (
            self.answer["type"] == "Date"
            and "minimum" in self.answer
            and "maximum" in self.answer
        ):
            if (
                "value" in self.answer["minimum"]
                and "value" in self.answer["maximum"]
                and not isinstance(self.answer["minimum"]["value"], dict)
                and not isinstance(self.answer["maximum"]["value"], dict)
            ):
                minimum_date = self._get_offset_date(self.answer["minimum"])
                maximum_date = self._get_offset_date(self.answer["maximum"])

                return minimum_date < maximum_date
        return True

    def validate_numeric_answer_value(self):
        min_value = self.answer.get("minimum", {}).get("value", 0)
        max_value = self.answer.get("maximum", {}).get("value", 0)

        if isinstance(min_value, int) and min_value < self.MIN_NUMBER:
            self.add_error(
                self.MINIMUM_LESS_THAN_LIMIT, value=min_value, limit=self.MIN_NUMBER
            )

        if isinstance(max_value, int) and max_value > self.MAX_NUMBER:
            self.add_error(
                self.MAXIMUM_GREATER_THAN_LIMIT, value=max_value, limit=self.MAX_NUMBER
            )

    def validate_numeric_answer_decimals(self):
        decimal_places = self.answer.get("decimal_places", 0)
        if decimal_places > self.MAX_DECIMAL_PLACES:
            self.add_error(
                self.DECIMAL_PLACES_TOO_LONG,
                decimal_places=decimal_places,
                limit=self.MAX_DECIMAL_PLACES,
            )

    def _validate_answer_actions(self):
        for option in self.options:

            action_params = option.get("action", {}).get("params")
            if not action_params:
                continue

            list_name = action_params.get("list_name")

            if list_name and list_name not in self.list_names:
                self.add_error(self.LIST_NAME_MISSING, list_name=list_name)

            block_id = action_params.get("block_id")

            if block_id and block_id not in self.block_ids:
                self.add_error(self.BLOCK_ID_MISSING, block_id=block_id)

    def has_default_route(self):
        for rule in self.block["routing_rules"]:
            if "goto" not in rule or "when" not in rule["goto"].keys():
                return True
        return False

    def _validate_routing_on_answer_options(self):
        answer_errors = []
        if (
            self.block
            and "routing_rules" in self.block
            and self.block["routing_rules"]
            and self.options
        ):
            option_values = [option["value"] for option in self.options]

            for rule in self.block["routing_rules"]:
                if "goto" in rule and "when" in rule["goto"].keys():
                    when_clause = rule["goto"]["when"]
                    for when in when_clause:
                        if (
                            "id" in when
                            and "value" in when
                            and when["id"] == self.answer["id"]
                            and when["value"] in option_values
                        ):
                            option_values.remove(when["value"])
                else:
                    option_values = []

            has_unrouted_options = option_values and len(option_values) != len(
                self.options
            )

            if self.answer["mandatory"] is False and not self.has_default_route():
                default_route_not_defined = "Default route not defined for optional question [{}]".format(
                    self.answer["id"]
                )
                answer_errors.append(default_route_not_defined)

            if has_unrouted_options:
                unrouted_error_template = (
                    "Routing rule not defined for all answers or default not defined "
                    "for answer [{}] missing options {}"
                )
                unrouted_error = unrouted_error_template.format(
                    self.answer["id"], option_values
                )
                answer_errors.append(unrouted_error)
        return answer_errors

    def validate_numeric_answer_types(self, answer_ranges):
        """
        Validate numeric answer types are valid.
        :return: list of dictionaries containing error messages, otherwise it returns an empty list
        """
        # Validate referred numeric answer exists (skip further tests for answer if error is returned)
        referred_errors = self._validate_referred_numeric_answer(answer_ranges)

        if referred_errors:
            return

        # Validate numeric answer has a positive range of possible responses
        self._validate_numeric_range(answer_ranges)

        # Validate referred numeric answer decimals
        self._validate_referred_numeric_answer_decimals(answer_ranges)

    def _validate_referred_numeric_answer(self, answer_ranges):
        """
        Referred will only be in answer_ranges if it's of a numeric type and appears earlier in the schema
        If either of the above is true then it will not have been given a value by _get_numeric_range_values
        """
        if answer_ranges[self.answer.get("id")]["min"] is None:
            self.add_error(
                'The referenced answer "{}" can not be used to set the minimum of answer "{}"'.format(
                    self.answer["minimum"]["value"]["identifier"], self.answer["id"]
                )
            )
            return True
        if answer_ranges[self.answer.get("id")]["max"] is None:
            self.add_error(
                'The referenced answer "{}" can not be used to set the maximum of answer "{}"'.format(
                    self.answer["maximum"]["value"]["identifier"], self.answer["id"]
                )
            )
            return True
        return False

    def _validate_numeric_range(self, answer_ranges):
        max_value = answer_ranges[self.answer.get("id")]["max"]
        min_value = answer_ranges[self.answer.get("id")]["min"]

        if max_value - min_value < 0:
            self.add_error(
                'Invalid range of min = {} and max = {} is possible for answer "{}".'.format(
                    min_value, max_value, self.answer["id"]
                )
            )

    def _validate_referred_numeric_answer_decimals(self, answer_ranges):
        answer_values = answer_ranges[self.answer["id"]]

        if answer_values["min_referred"] is not None:
            referred_values = answer_ranges[answer_values["min_referred"]]
            if answer_values["decimal_places"] < referred_values["decimal_places"]:
                self.add_error(
                    'The referenced answer "{}" has a greater number of decimal places than answer "{}"'.format(
                        answer_values["min_referred"], self.answer["id"]
                    )
                )

        if answer_values["max_referred"] is not None:
            referred_values = answer_ranges[answer_values["max_referred"]]
            if answer_values["decimal_places"] < referred_values["decimal_places"]:
                self.add_error(
                    'The referenced answer "{}" has a greater number of decimal places than answer "{}"'.format(
                        answer_values["max_referred"], self.answer["id"]
                    )
                )

    def get_numeric_range_values(self, answer_ranges):
        min_value = self.answer.get("minimum", {}).get("value", {})
        max_value = self.answer.get("maximum", {}).get("value", {})
        min_referred = (
            min_value.get("identifier") if isinstance(min_value, dict) else None
        )
        max_referred = (
            max_value.get("identifier") if isinstance(max_value, dict) else None
        )

        exclusive = self.answer.get("exclusive", False)
        decimal_places = self.answer.get("decimal_places", 0)

        return {
            "min": self._get_answer_minimum(
                min_value, decimal_places, exclusive, answer_ranges
            ),
            "max": self._get_answer_maximum(
                max_value, decimal_places, exclusive, answer_ranges
            ),
            "decimal_places": decimal_places,
            "min_referred": min_referred,
            "max_referred": max_referred,
            "default": self.answer.get("default"),
        }

    def _get_answer_minimum(
        self, defined_minimum, decimal_places, exclusive, answer_ranges
    ):
        minimum_value = self._get_numeric_value(defined_minimum, 0, answer_ranges)
        if exclusive:
            return minimum_value + (1 / 10 ** decimal_places)
        return minimum_value

    def _get_answer_maximum(
        self, defined_maximum, decimal_places, exclusive, answer_ranges
    ):
        maximum_value = self._get_numeric_value(
            defined_maximum, self.MAX_NUMBER, answer_ranges
        )
        if exclusive:
            return maximum_value - (1 / 10 ** decimal_places)
        return maximum_value

    @staticmethod
    def _get_numeric_value(defined_value, system_default, answer_ranges):
        if not isinstance(defined_value, dict):
            return defined_value
        if "source" in defined_value and defined_value["source"] == "answers":
            referred_answer = answer_ranges.get(defined_value["identifier"])
            if referred_answer is None:
                # Referred answer is not valid (picked up by _validate_referred_numeric_answer)
                return None
            if referred_answer.get("default") is not None:
                return system_default
        return system_default

    def validate_numeric_default(self):
        if self.answer.get("mandatory") and self.answer.get("default") is not None:
            self.add_error(self.DEFAULT_ON_MANDATORY)

    def _get_offset_date(self, answer_min_or_max):
        if answer_min_or_max["value"] == "now":
            value = datetime.utcnow().strftime("%Y-%m-%d")
        else:
            value = answer_min_or_max["value"]

        offset = answer_min_or_max.get("offset_by", {})

        return self.get_relative_date(value, offset)

    @staticmethod
    def get_relative_date(date_string, offset_object):
        # Returns a relative date given an offset or period object
        return AnswerValidator._convert_to_datetime(date_string) + relativedelta(
            years=offset_object.get("years", 0),
            months=offset_object.get("months", 0),
            days=offset_object.get("days", 0),
        )

    @staticmethod
    def _convert_to_datetime(value):
        date_format = "%Y-%m"
        if value and re.match(r"\d{4}-\d{2}-\d{2}", value):
            date_format = "%Y-%m-%d"

        return datetime.strptime(value, date_format) if value else None

    def is_suggestion_url_valid(self):
        parsed_result = urlparse(self.answer["suggestions_url"])

        if parsed_result.scheme and parsed_result.netloc:
            return True
        return re.match(r"^[A-Za-z0-9_.\-/~]+$", parsed_result.path) is not None