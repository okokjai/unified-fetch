#!/usr/bin/env python3
"""
test_identity_loading.py — TDD tests for IdentityManager profile loading.

Tests that profiles survive across IdentityManager instances (persistence).
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser.identity import IdentityManager


async def test_profiles_survive_restart():
    """Profiles created in one session should be available in the next."""
    data_dir = tempfile.mkdtemp(prefix="identity_test_")

    # Session 1: Create profiles
    mgr1 = IdentityManager(data_dir)
    for p in mgr1.factory.synthesize(count=3):
        mgr1.add_profile(p)

    stats1 = mgr1.get_stats()
    assert stats1["total_profiles"] == 3, f"Expected 3 profiles, got {stats1['total_profiles']}"
    assert stats1["loaded_profiles"] == 3, f"Expected 3 loaded, got {stats1['loaded_profiles']}"
    mgr1.close()

    # Session 2: Load existing profiles from store
    mgr2 = IdentityManager(data_dir)
    stats2 = mgr2.get_stats()
    assert stats2["total_profiles"] == 3, f"Store has 3 but loaded_profiles={stats2['loaded_profiles']}"
    assert stats2["loaded_profiles"] == 3, f"Expected 3 loaded in memory, got {stats2['loaded_profiles']}"

    # Should be able to get a profile for a site
    profile, is_new = mgr2.get_profile_for_site("example.com")
    assert profile is not None, "get_profile_for_site returned None"
    assert profile.name, "Profile has no name"

    mgr2.close()
    print("PASS: test_profiles_survive_restart")


async def test_empty_store_creates_profiles():
    """Fresh data dir should allow profile creation."""
    data_dir = tempfile.mkdtemp(prefix="identity_test_empty_")
    mgr = IdentityManager(data_dir)

    stats = mgr.get_stats()
    assert stats["total_profiles"] == 0, "Fresh store should be empty"
    assert stats["loaded_profiles"] == 0, "No profiles loaded in memory yet"

    # Create profiles
    for p in mgr.factory.synthesize(count=2):
        mgr.add_profile(p)

    stats2 = mgr.get_stats()
    assert stats2["total_profiles"] == 2
    assert stats2["loaded_profiles"] == 2

    # get_profile_for_site should work
    profile, is_new = mgr.get_profile_for_site("github.com")
    assert profile is not None

    mgr.close()
    print("PASS: test_empty_store_creates_profiles")


async def main():
    print("=== Identity Loading Tests ===\n")

    print("Test 1: Profiles survive restart...")
    try:
        await test_profiles_survive_restart()
    except AssertionError as e:
        print(f"  FAIL (expected): {e}")
        print("  -> This confirms the bug: profiles in store but not loaded into memory")
    except Exception as e:
        print(f"  ERROR: {e}")

    print()
    print("Test 2: Empty store creates profiles...")
    try:
        await test_empty_store_creates_profiles()
    except AssertionError as e:
        print(f"  FAIL: {e}")
    except Exception as e:
        print(f"  ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(main())
