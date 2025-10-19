"""Configuration validation to prevent accidental writes to production."""

import os
from typing import Optional


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""

    pass


class ConfigValidator:
    """Validates configuration to prevent production accidents."""

    # Buckets that should NEVER be written to during testing
    PRODUCTION_BUCKETS = {"temperatures", "weather", "spotprice", "emeters", "checkwatt_full_data"}

    # Test bucket patterns
    TEST_BUCKET_SUFFIX = "_test"

    # Field patterns that indicate test data
    TEST_FIELD_PATTERNS = ["Test", "test", "dummy", "Dummy", "fake", "Fake"]

    @classmethod
    def is_production_bucket(cls, bucket_name: str) -> bool:
        """
        Check if bucket is a production bucket.

        Args:
            bucket_name: Name of the bucket

        Returns:
            True if this is a production bucket
        """
        return bucket_name in cls.PRODUCTION_BUCKETS

    @classmethod
    def is_test_bucket(cls, bucket_name: str) -> bool:
        """
        Check if bucket is a test bucket.

        Args:
            bucket_name: Name of the bucket

        Returns:
            True if this is a test bucket
        """
        return bucket_name.endswith(cls.TEST_BUCKET_SUFFIX)

    @classmethod
    def validate_field_names(cls, fields: dict, allow_test_fields: bool = True) -> list[str]:
        """
        Check if any field names look like test data.

        Args:
            fields: Dictionary of field names to values
            allow_test_fields: If False, raise error on test field names

        Returns:
            List of field names that look like test data

        Raises:
            ConfigValidationError: If test fields found and not allowed
        """
        test_fields = []

        for field_name in fields.keys():
            for pattern in cls.TEST_FIELD_PATTERNS:
                if pattern in field_name:
                    test_fields.append(field_name)
                    break

        if test_fields and not allow_test_fields:
            raise ConfigValidationError(
                f"Test field names detected: {', '.join(test_fields)}. "
                f"This data should not be written to production buckets!"
            )

        return test_fields

    @classmethod
    def validate_write(cls, bucket: str, fields: dict, strict_mode: bool = False) -> Optional[str]:
        """
        Validate a write operation before executing.

        Args:
            bucket: Target bucket name
            fields: Dictionary of fields to write
            strict_mode: If True, prevent all writes to production buckets

        Returns:
            Warning message if validation concerns exist, None if OK

        Raises:
            ConfigValidationError: If validation fails and write should be blocked
        """
        warnings = []

        # Check if writing to production bucket
        is_prod = cls.is_production_bucket(bucket)

        if is_prod:
            warnings.append(f"WARNING: Writing to PRODUCTION bucket: {bucket}")

            # Check for test field names
            test_fields = cls.validate_field_names(fields, allow_test_fields=False)

            if test_fields:
                raise ConfigValidationError(
                    f"Attempting to write test fields {test_fields} "
                    f"to PRODUCTION bucket '{bucket}'! "
                    f"This is likely a configuration error."
                )

            if strict_mode:
                raise ConfigValidationError(
                    f"Strict mode is enabled. "
                    f"Refusing to write to production bucket '{bucket}'. "
                    f"Use test buckets or disable strict mode."
                )

        # Check if using test bucket
        if cls.is_test_bucket(bucket):
            test_fields = cls.validate_field_names(fields, allow_test_fields=True)
            if test_fields:
                warnings.append(f"INFO: Writing test fields {test_fields} to test bucket {bucket}")

        return " | ".join(warnings) if warnings else None

    @classmethod
    def check_environment(cls, config) -> list[str]:
        """
        Check entire configuration for production/test bucket usage.

        Args:
            config: Configuration object

        Returns:
            List of warnings/info messages about the configuration
        """
        messages = []

        buckets_to_check = [
            ("temperatures", config.influxdb_bucket_temperatures),
            ("weather", config.influxdb_bucket_weather),
            ("spotprice", config.influxdb_bucket_spotprice),
            ("emeters", config.influxdb_bucket_emeters),
            ("checkwatt", config.influxdb_bucket_checkwatt),
        ]

        prod_count = 0
        test_count = 0

        for bucket_type, bucket_name in buckets_to_check:
            if cls.is_production_bucket(bucket_name):
                messages.append(f"  {bucket_type}: {bucket_name} (PRODUCTION)")
                prod_count += 1
            elif cls.is_test_bucket(bucket_name):
                messages.append(f"  {bucket_type}: {bucket_name} (TEST)")
                test_count += 1
            else:
                messages.append(f"  {bucket_type}: {bucket_name} (UNKNOWN)")

        if prod_count > 0 and test_count > 0:
            messages.insert(
                0,
                "WARNING: Mixed production and test buckets! "
                "This is unusual and may indicate configuration error.",
            )
        elif prod_count == len(buckets_to_check):
            messages.insert(0, "PRODUCTION environment detected")
        elif test_count == len(buckets_to_check):
            messages.insert(0, "TEST environment detected")

        return messages

    @classmethod
    def get_strict_mode(cls) -> bool:
        """
        Check if strict mode is enabled via environment variable.

        Strict mode prevents ALL writes to production buckets.

        Returns:
            True if STRICT_MODE=1 or STRICT_MODE=true
        """
        strict = os.environ.get("STRICT_MODE", "").lower()
        return strict in ("1", "true", "yes", "on")

    @classmethod
    def require_test_environment(cls, config) -> None:
        """
        Require that ALL buckets are test buckets.

        Useful for automated tests and CI/CD.

        Args:
            config: Configuration object

        Raises:
            ConfigValidationError: If any production bucket is configured
        """
        buckets = [
            config.influxdb_bucket_temperatures,
            config.influxdb_bucket_weather,
            config.influxdb_bucket_spotprice,
            config.influxdb_bucket_emeters,
            config.influxdb_bucket_checkwatt,
        ]

        prod_buckets = [b for b in buckets if cls.is_production_bucket(b)]

        if prod_buckets:
            raise ConfigValidationError(
                f"Test environment required, but production buckets detected: "
                f"{', '.join(prod_buckets)}. "
                f"Please use .env.test configuration for testing."
            )
