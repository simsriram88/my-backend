from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# ── Driver (singleton) ────────────────────────────────────────────────────────
driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD)
)


def test_graphdb_connection() -> bool:
    """Test connection to Neo4j Aura before starting the app."""
    try:
        with driver.session() as session:
            result = session.run("RETURN 1 AS ping").single()
            if result and result["ping"] == 1:
                print("  GraphDB connection successful.")
                return True
    except AuthError as e:
        print(f"  GraphDB AUTH ERROR: {e}")
    except ServiceUnavailable as e:
        print(f"  GraphDB CONNECTION ERROR: {e}")
    except Exception as e:
        print(f"  GraphDB UNEXPECTED ERROR: {e}")
    return False

def get_blueprint_parameters(blueprint_id: str, resource_name: str) -> list:
    """
    Fetches Parameter nodes for a given blueprint and resource type.
    Uses CONTAINS matching on resource name for flexibility.
    Returns all parameters with their details.
    """
    query = """
        MATCH (b:BlueprintTemplate { blueprint_id: $blueprint_id })
              -[:INCLUDES_RESOURCE]->(r:ResourceType)
              -[:REQUIRES_PARAM]->(p:Parameter)
        WHERE toLower(r.name) CONTAINS toLower($resource_name)
           OR toLower($resource_name) CONTAINS toLower(r.name)
        RETURN
            r.name              AS resource_type,
            p.key               AS param_key,
            p.parameter_type    AS parameter_type,
            p.required          AS required,
            p.defaultValue      AS default_value
        ORDER BY r.name, p.key
    """
    results = []
    with driver.session() as session:
        records = session.run(
            query,
            blueprint_id  = blueprint_id,
            resource_name = resource_name
        )
        for record in records:
            results.append({
                "resource_type"  : record["resource_type"],
                "param_key"      : record["param_key"],
                "parameter_type" : record["parameter_type"],
                "required"       : record["required"],
                "default_value"  : record["default_value"],
            })
    return results


def get_mandatory_params_for_all_resources(blueprint_id: str, resource_names: list) -> dict:
    """
    For each resource name, fetch all parameters from GraphDB
    and filter to only mandatory ones.

    Returns:
    {
        "AzureDataFactory": ["param_key1", "param_key2", ...],
        "StorageAccount":   ["param_key1", "param_key2", ...],
    }
    """
    mandatory_params = {}
    for resource_name in resource_names:
        all_params = get_blueprint_parameters(blueprint_id, resource_name)
        mandatory  = [
            p["param_key"]
            for p in all_params
            if p["parameter_type"] == "mandatory"
        ]
        if mandatory:
            mandatory_params[resource_name] = mandatory
        print(f"  Mandatory params for {resource_name}: {mandatory}")
    return mandatory_params

def find_blueprints_for_resource(resource_name: str) -> list:
    
    query = """
        // Exact match first
        MATCH (r:ResourceType)
        WHERE toLower(r.name) = toLower($resource_name)

        WITH r, "exact" AS match_type

        // Traverse to blueprints
        MATCH (b:BlueprintTemplate)-[rel:INCLUDES_RESOURCE]->(r)
        RETURN
            r.name              AS matched_resource_type,
            r.category          AS resource_category,
            match_type,
            b.blueprint_id      AS blueprint_id,
            b.version           AS blueprint_version,
            b.source            AS blueprint_source,
            rel.blueprint_mandatory AS blueprint_mandatory

        UNION

        // Contains match — covers partial names
        // e.g. user says "Storage" and GraphDB has "StorageAccount"
        MATCH (r:ResourceType)
        WHERE toLower(r.name) CONTAINS toLower($resource_name)
           OR toLower($resource_name) CONTAINS toLower(r.name)

        WITH r, "contains" AS match_type

        MATCH (b:BlueprintTemplate)-[rel:INCLUDES_RESOURCE]->(r)
        RETURN
            r.name              AS matched_resource_type,
            r.category          AS resource_category,
            match_type,
            b.blueprint_id      AS blueprint_id,
            b.version           AS blueprint_version,
            b.source            AS blueprint_source,
            rel.blueprint_mandatory AS blueprint_mandatory

        ORDER BY match_type ASC, blueprint_id ASC
    """

    results = []
    seen = set()  # deduplicate across UNION

    with driver.session() as session:
        records = session.run(query, resource_name=resource_name)
        for record in records:
            key = (record["matched_resource_type"], record["blueprint_id"])
            if key not in seen:
                seen.add(key)
                results.append({
                    "matched_resource_type" : record["matched_resource_type"],
                    "resource_category"     : record["resource_category"],
                    "match_type"            : record["match_type"],
                    "blueprint_id"          : record["blueprint_id"],
                    "blueprint_version"     : record["blueprint_version"],
                    "blueprint_source"      : record["blueprint_source"],
                    "blueprint_mandatory"   : record["blueprint_mandatory"],
                })
    print(f"\n all blueprints: {results}")
    return results


def find_blueprints_for_multiple_resources(resource_names: list) -> dict:
    """
    Run find_blueprints_for_resource for each resource name
    in the intermediate JSON and return a consolidated result.

    Returns:
    {
        "StorageAccount": [ { blueprint_id, match_type, ... }, ... ],
        "AzureKeyVault":  [ { blueprint_id, match_type, ... }, ... ],
        "Synapse":        []   <- empty means no match found in GraphDB
    }
    """
    consolidated = {}
    for name in resource_names:
        matches = find_blueprints_for_resource(name)
        consolidated[name] = matches
    return consolidated


def get_common_blueprints(blueprint_matches: dict) -> list:
    """
    Given the consolidated blueprint matches per resource,
    find blueprints that cover ALL requested resources —
    i.e. the intersection across all resource match sets.

    Returns list of blueprint_ids that appear for every resource.
    If none cover all, returns empty list (agent will suggest best combination).
    """
    if not blueprint_matches:
        return []

    # Get set of blueprint_ids per resource
    sets = []
    for resource, matches in blueprint_matches.items():
        blueprint_ids = {m["blueprint_id"] for m in matches}
        sets.append(blueprint_ids)

    if not sets:
        return []

    # Intersection — blueprints covering ALL resources
    common = sets[0].intersection(*sets[1:])
    return list(common)