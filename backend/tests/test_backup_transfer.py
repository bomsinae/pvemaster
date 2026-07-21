from app.services.backup_runner import backup_transfer_bytes


def test_backup_transfer_bytes_excludes_reused_data() -> None:
    assert (
        backup_transfer_bytes(
            [
                {"n": 1, "t": "INFO: backup was done incrementally, reused 15.75 GiB (98%)"},
                {"n": 2, "t": "INFO: transferred 16.00 GiB in 8 seconds"},
            ]
        )
        == 256 * 1024**2
    )


def test_backup_transfer_bytes_supports_full_and_missing_measurements() -> None:
    assert backup_transfer_bytes([{"t": "INFO: transferred 512 MiB in 2 seconds"}]) == 512 * 1024**2
    assert backup_transfer_bytes([{"t": "INFO: backup finished successfully"}]) is None


def test_backup_transfer_bytes_does_not_claim_zero_from_rounded_values() -> None:
    assert (
        backup_transfer_bytes(
            [
                {"t": "INFO: backup was done incrementally, reused 16.00 GiB (99%)"},
                {"t": "INFO: transferred 16.00 GiB in 8 seconds"},
            ]
        )
        is None
    )
    assert (
        backup_transfer_bytes(
            [
                {"t": "INFO: backup was done incrementally, reused 16.00 GiB (100%)"},
                {"t": "INFO: transferred 16.00 GiB in 8 seconds"},
            ]
        )
        == 0
    )
