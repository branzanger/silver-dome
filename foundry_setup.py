#!/usr/bin/env python3
"""
Silver Dome — Palantir Foundry Ontology Setup

Run this ONCE to create the ThreatObject type and action in Foundry.
Requires a valid FOUNDRY_TOKEN environment variable.

Usage:
    FOUNDRY_TOKEN=<your-token> python foundry_setup.py

To get a token:
    1. Go to Foundry → Settings → Tokens
    2. Create a new token with "ontology:edit" and "api:write" scopes
    3. Copy the token
"""
import json
import os
import sys

import requests

BASE_URL = os.getenv("FOUNDRY_BASE_URL", "https://mukund.usw-18.palantirfoundry.com")
TOKEN = os.getenv("FOUNDRY_TOKEN", "")

if not TOKEN:
    print("ERROR: Set FOUNDRY_TOKEN environment variable first.")
    print("  export FOUNDRY_TOKEN='your-token-here'")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}


def api_get(path):
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    print(f"  GET {path} → {resp.status_code}")
    return resp


def api_post(path, body):
    url = f"{BASE_URL}{path}"
    resp = requests.post(url, headers=HEADERS, json=body, timeout=15)
    print(f"  POST {path} → {resp.status_code}")
    if not resp.ok:
        print(f"    Error: {resp.text[:500]}")
    return resp


def list_ontologies():
    """List available ontologies to find the right one."""
    resp = api_get("/api/v2/ontologies")
    if resp.ok:
        data = resp.json()
        ontologies = data.get("data", [])
        print(f"\nFound {len(ontologies)} ontologie(s):")
        for ont in ontologies:
            print(f"  - {ont.get('displayName', 'unnamed')}: rid={ont.get('rid', '?')}")
            print(f"    apiName: {ont.get('apiName', '?')}")
        return ontologies
    return []


def check_token():
    """Verify the token works."""
    print("Checking token...")
    resp = api_get("/api/v2/ontologies")
    if resp.ok:
        print("  Token is valid!")
        return True
    print(f"  Token check failed: {resp.status_code}")
    print(f"  {resp.text[:300]}")
    return False


def main():
    print("=" * 50)
    print("Silver Dome — Foundry Setup")
    print(f"Stack: {BASE_URL}")
    print("=" * 50)
    print()

    # Step 1: Verify token
    if not check_token():
        sys.exit(1)

    # Step 2: List ontologies
    print()
    ontologies = list_ontologies()

    if not ontologies:
        print("\nNo ontologies found. You need to create one in the Foundry UI first:")
        print("  1. Go to Foundry → Ontology Manager")
        print("  2. Create a new ontology (or use the default one)")
        print("  3. Note the ontology RID")
        print("  4. Set FOUNDRY_ONTOLOGY_ID=<rid> and re-run this script")
        sys.exit(1)

    # Step 3: Show what to configure
    print()
    print("=" * 50)
    print("NEXT STEPS:")
    print("=" * 50)
    print()
    print("1. In Foundry Ontology Manager, create an object type 'ThreatObject' with these properties:")
    print()
    print("   Property Name     | Type     | Description")
    print("   ------------------|----------|---------------------------")
    print("   threatId          | String   | Primary key (UUID)")
    print("   timestamp         | String   | ISO 8601 timestamp")
    print("   confidence        | Double   | Fused confidence 0.0-1.0")
    print("   bearingDeg        | Double   | Bearing in degrees 0-360")
    print("   rangeM            | Double   | Distance in meters")
    print("   tiltDeg           | Double   | Tilt angle in degrees")
    print("   activeSensors     | String[] | Array of sensor types")
    print("   threatLevel       | String   | LOW / MEDIUM / HIGH")
    print("   siteId            | String   | Deployment site ID")
    print("   status            | String   | ACTIVE / TRACKING / CLEARED")
    print()
    print("2. Create an Action Type 'create-threat-object' that creates a ThreatObject")
    print()
    print("3. Set these environment variables:")
    print(f"   export FOUNDRY_BASE_URL='{BASE_URL}'")
    print(f"   export FOUNDRY_TOKEN='{TOKEN[:8]}...'")

    if ontologies:
        ont = ontologies[0]
        rid = ont.get("rid", "")
        print(f"   export FOUNDRY_ONTOLOGY_ID='{rid}'")
    else:
        print("   export FOUNDRY_ONTOLOGY_ID='<ontology-rid>'")

    print("   export FOUNDRY_OBJECT_TYPE='ThreatObject'")
    print()
    print("4. Then run: python main.py")
    print()

    # Step 4: Test write if ontology ID is set
    ont_id = os.getenv("FOUNDRY_ONTOLOGY_ID", "")
    if ont_id:
        print("Testing Foundry write...")
        from models import ThreatObject
        test_threat = ThreatObject(
            confidence=0.75,
            bearing_deg=90.0,
            range_m=2.5,
            active_sensors=["rf", "pir"],
            threat_level="MEDIUM",
            site_id="SETUP-TEST",
            status="ACTIVE",
        )
        resp = api_post(
            f"/api/v2/ontologies/{ont_id}/actions/create-threat-object",
            {"parameters": test_threat.to_dict()},
        )
        if resp.ok:
            print("  Test write succeeded!")
        else:
            print("  Test write failed — you may need to create the Action Type first.")


if __name__ == "__main__":
    main()
