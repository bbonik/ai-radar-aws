"""Unit tests for the Config module."""

import pytest

from src.config import Config


class TestConfigDefaults:
    """Test that Config can be instantiated with all default values."""

    def test_instantiation_without_arguments(self):
        """Config() should instantiate without any arguments."""
        config = Config()
        assert config is not None

    def test_aws_region_default(self):
        assert Config().aws_region == "us-east-1"

    def test_schedule_hour_default(self):
        assert Config().schedule_hour == 22

    def test_schedule_minute_default(self):
        assert Config().schedule_minute == 0

    def test_llm_a_model_id_default(self):
        assert Config().llm_a_model_id == "global.anthropic.claude-sonnet-4-6"

    def test_llm_a_temperature_default(self):
        assert Config().llm_a_temperature == 0.3

    def test_llm_a_max_tokens_default(self):
        assert Config().llm_a_max_tokens == 4096

    def test_llm_a_inference_profile_name_default(self):
        assert Config().llm_a_inference_profile_name == "ai-radar-report-generator"

    def test_llm_b_model_id_default(self):
        assert Config().llm_b_model_id == "global.anthropic.claude-opus-4-6-v1"

    def test_llm_b_temperature_default(self):
        assert Config().llm_b_temperature == 0.2

    def test_llm_b_max_tokens_default(self):
        assert Config().llm_b_max_tokens == 2048

    def test_llm_b_inference_profile_name_default(self):
        assert Config().llm_b_inference_profile_name == "ai-radar-graph-generator"

    def test_service_points_high_default(self):
        assert Config().service_points_high == 3

    def test_service_points_medium_default(self):
        assert Config().service_points_medium == 2

    def test_service_points_base_default(self):
        assert Config().service_points_base == 1

    def test_blogpost_points_default(self):
        assert Config().blogpost_points == 2

    def test_word_count_scale_default(self):
        assert Config().word_count_scale == 0.005

    def test_threshold_2_star_default(self):
        assert Config().threshold_2_star == 3.0

    def test_threshold_3_star_default(self):
        assert Config().threshold_3_star == 5.0

    def test_research_timeout_default(self):
        assert Config().research_timeout_per_announcement == 300

    def test_rss_url_default(self):
        assert Config().rss_url == "https://aws.amazon.com/about-aws/whats-new/recent/feed/"

    def test_rss_fetch_timeout_default(self):
        assert Config().rss_fetch_timeout == 30

    def test_rss_max_retries_default(self):
        assert Config().rss_max_retries == 3

    def test_website_builder_function_name_default(self):
        assert Config().website_builder_function_name == "ai-radar-website-builder"

    def test_website_builder_timeout_default(self):
        assert Config().website_builder_timeout == 600


class TestConfigTypes:
    """Test that all configuration fields are correctly typed."""

    @pytest.fixture
    def config(self):
        return Config()

    def test_string_fields(self, config):
        string_fields = [
            config.aws_region,
            config.llm_a_model_id,
            config.llm_a_inference_profile_name,
            config.llm_b_model_id,
            config.llm_b_inference_profile_name,
            config.report_prompt_template,
            config.graph_prompt_template,
            config.rss_url,
            config.website_builder_function_name,
        ]
        for value in string_fields:
            assert isinstance(value, str)

    def test_int_fields(self, config):
        int_fields = [
            config.schedule_hour,
            config.schedule_minute,
            config.llm_a_max_tokens,
            config.llm_b_max_tokens,
            config.service_points_high,
            config.service_points_medium,
            config.service_points_base,
            config.blogpost_points,
            config.research_timeout_per_announcement,
            config.rss_fetch_timeout,
            config.rss_max_retries,
            config.website_builder_timeout,
        ]
        for value in int_fields:
            assert isinstance(value, int)

    def test_float_fields(self, config):
        float_fields = [
            config.llm_a_temperature,
            config.llm_b_temperature,
            config.word_count_scale,
            config.threshold_2_star,
            config.threshold_3_star,
        ]
        for value in float_fields:
            assert isinstance(value, float)


class TestConfigCustomValues:
    """Test that Config can be instantiated with custom overrides."""

    def test_override_aws_region(self):
        config = Config(aws_region="eu-west-1")
        assert config.aws_region == "eu-west-1"

    def test_override_schedule_hour(self):
        config = Config(schedule_hour=8)
        assert config.schedule_hour == 8

    def test_override_llm_a_temperature(self):
        config = Config(llm_a_temperature=0.7)
        assert config.llm_a_temperature == 0.7

    def test_override_multiple_fields(self):
        config = Config(
            aws_region="ap-southeast-1",
            schedule_hour=6,
            llm_a_max_tokens=8192,
            threshold_3_star=7.0,
        )
        assert config.aws_region == "ap-southeast-1"
        assert config.schedule_hour == 6
        assert config.llm_a_max_tokens == 8192
        assert config.threshold_3_star == 7.0

    def test_override_does_not_affect_other_defaults(self):
        config = Config(aws_region="eu-west-1")
        assert config.schedule_hour == 22
        assert config.rss_url == "https://aws.amazon.com/about-aws/whats-new/recent/feed/"


class TestConfigPromptTemplates:
    """Test that prompt templates are non-empty strings."""

    @pytest.fixture
    def config(self):
        return Config()

    def test_report_prompt_template_is_non_empty(self, config):
        assert isinstance(config.report_prompt_template, str)
        assert len(config.report_prompt_template) > 0

    def test_graph_prompt_template_is_non_empty(self, config):
        assert isinstance(config.graph_prompt_template, str)
        assert len(config.graph_prompt_template) > 0

    def test_report_prompt_contains_placeholders(self, config):
        """Report prompt template should contain format placeholders."""
        assert "{title}" in config.report_prompt_template
        assert "{description}" in config.report_prompt_template

    def test_graph_prompt_contains_placeholders(self, config):
        """Graph prompt template should contain format placeholders."""
        assert "{title}" in config.graph_prompt_template
        assert "{description}" in config.graph_prompt_template
