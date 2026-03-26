import json
from neo4j import GraphDatabase, TrustAll
from neo4j.exceptions import ServiceUnavailable, AuthError


# ── Config ────────────────────────────────────────────────────────────────────
NEO4J_URI      = "neo4j+ssc://2468a757.databases.neo4j.io"   # replace with your Neo4j URI
NEO4J_USER     = "2468a757"
NEO4J_PASSWORD = "sv5kX0YjvKSAtP7z8KSSaJnyRGmPN196ezhFSIb_bsE"
DATA_FILE      = "onetimeload.json"

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# ── Connection Test ───────────────────────────────────────────────────────────
def test_connection():
    print("Testing connection to Neo4j Aura...")
    try:
        with driver.session() as session:
            result = session.run("RETURN 1 AS ping")
            record = result.single()
            if record and record["ping"] == 1:
                print("  Connection successful.")

            # Also fetch server info for confirmation
            info = session.run("""
                CALL dbms.components()
                YIELD name, versions, edition
                RETURN name, versions[0] AS version, edition
            """)
            for row in info:
                print(f"  Server     : {row['name']}")
                print(f"  Version    : {row['version']}")
                print(f"  Edition    : {row['edition']}")

        return True

    except AuthError as e:
        print(f"  AUTH ERROR: Invalid credentials — {e}")
        return False

    except ServiceUnavailable as e:
        print(f"  CONNECTION ERROR: Could not reach Neo4j Aura — {e}")
        print("  Check your URI, and ensure your IP is not blocked by Aura firewall rules.")
        return False

    except Exception as e:
        print(f"  UNEXPECTED ERROR: {e}")
        return False


# ── Load Functions ────────────────────────────────────────────────────────────
def create_resource_type_and_params(tx, resource):
    tx.run("""
        MERGE (r:ResourceType {
            name:     $name,
            provider: $provider,
            category: $category
        })
    """,
    name     = resource["resource_type"],
    provider = resource["provider"],
    category = resource["category"],
    )

    for param in resource["parameters"]:
        tx.run("""
            MATCH (r:ResourceType { name: $resource_type })

            MERGE (p:Parameter {
                key:           $key,
                resource_type: $resource_type
            })
            SET
                p.required       = $required,
                p.defaultValue   = $default_value,
                p.parameter_type = $parameter_type

            MERGE (r)-[:REQUIRES_PARAM]->(p)
        """,
        resource_type  = resource["resource_type"],
        key            = param["key"],
        required       = param["required"],
        default_value  = param["defaultValue"],
        parameter_type = param["parameter_type"],
        )


def create_blueprint_and_relationships(tx, blueprint):
    tx.run("""
        MERGE (b:BlueprintTemplate {
            blueprint_id: $blueprint_id
        })
        SET
            b.version    = $version,
            b.iac_format = $iac_format,
            b.source     = $source
    """,
    blueprint_id = blueprint["blueprint_id"],
    version      = blueprint["version"],
    iac_format   = blueprint["iac_format"],
    source       = blueprint["source"],
    )

    for rt in blueprint["resource_types"]:
        tx.run("""
            MATCH (b:BlueprintTemplate { blueprint_id: $blueprint_id })
            MATCH (r:ResourceType      { name: $resource_type })

            MERGE (b)-[rel:INCLUDES_RESOURCE]->(r)
            SET rel.blueprint_mandatory = $blueprint_mandatory
        """,
        blueprint_id        = blueprint["blueprint_id"],
        resource_type       = rt["resource_type"],
        blueprint_mandatory = rt["blueprint_mandatory"],
        )


# ── Data Load ─────────────────────────────────────────────────────────────────
def load_data(data_file):
    with open(data_file, "r") as f:
        data = json.load(f)

    with driver.session() as session:

        # Step 1 — ResourceType nodes and Parameters
        print("\nStep 1: Loading ResourceType nodes and Parameters...")
        for resource in data["resource_types"]:
            print(f"  -> {resource['resource_type']} ({len(resource['parameters'])} params)")
            session.execute_write(create_resource_type_and_params, resource)

        # Step 2 — BlueprintTemplate nodes and INCLUDES_RESOURCE edges
        print("\nStep 2: Loading BlueprintTemplate nodes and relationships...")
        for blueprint in data["blueprints"]:
            print(f"  -> {blueprint['blueprint_id']}")
            session.execute_write(create_blueprint_and_relationships, blueprint)

    print("\nData load complete.")


# ── Verify Load ───────────────────────────────────────────────────────────────
def verify_load():
    print("\nVerifying loaded data...")
    with driver.session() as session:

        # Count nodes
        counts = session.run("""
            MATCH (b:BlueprintTemplate) WITH count(b) AS blueprints
            MATCH (r:ResourceType)      WITH blueprints, count(r) AS resources
            MATCH (p:Parameter)         WITH blueprints, resources, count(p) AS params
            RETURN blueprints, resources, params
        """).single()
        print(f"  BlueprintTemplate nodes : {counts['blueprints']}")
        print(f"  ResourceType nodes      : {counts['resources']}")
        print(f"  Parameter nodes         : {counts['params']}")

        # Show blueprint -> resource relationships
        print("\n  Blueprint -> Resource Type mapping:")
        results = session.run("""
            MATCH (b:BlueprintTemplate)-[rel:INCLUDES_RESOURCE]->(r:ResourceType)
            RETURN b.blueprint_id AS blueprint,
                   r.name         AS resource,
                   rel.blueprint_mandatory AS mandatory
            ORDER BY rel.blueprint_mandatory DESC, r.name
        """)
        for row in results:
            flag = "MANDATORY" if row["mandatory"] else "optional"
            print(f"    {row['blueprint']:35s} -> {row['resource']:25s} [{flag}]")

        # Show parameters per resource
        print("\n  Parameters per ResourceType:")
        results = session.run("""
            MATCH (r:ResourceType)-[:REQUIRES_PARAM]->(p:Parameter)
            RETURN r.name          AS resource,
                   p.key           AS param_key,
                   p.parameter_type AS type,
                   p.required      AS required,
                   p.defaultValue  AS default_value
            ORDER BY r.name, p.key
        """)
        current_resource = None
        for row in results:
            if row["resource"] != current_resource:
                current_resource = row["resource"]
                print(f"\n    {current_resource}:")
            print(f"      {row['param_key']:45s} | {row['type']:10s} | required={row['required']} | default={row['default_value']}")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    # Always test connection first — exit early if it fails
    connected = test_connection()
    if not connected:
        print("\nAborting — fix connection issues before loading data.")
        driver.close()
        sys.exit(1)

    # Load data
    load_data(DATA_FILE)

    # Verify what was loaded
    verify_load()

    driver.close()
