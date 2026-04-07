"""
Church Carpool Optimizer

Install with:
    pip install streamlit requests folium streamlit-folium pandas

Run with:
    streamlit run carpool_app.py
"""

import math
import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium


# Set page config before any other Streamlit UI calls
st.set_page_config(page_title="Church Carpool Optimizer", page_icon="⛪", layout="wide")


# =========================================================
# Session State Initialization
# =========================================================
def init_session_state():
    defaults = {
        "ors_api_key": "",
        "church_address": "",
        "_church_address": "",
        "people": [],
        "geocode_cache": {},
        "carpool_results": None,
        "person_counter": 0,
        "new_person_name": "",
        "new_person_has_car": False,
        "new_person_capacity": 3,
        "new_person_address": "",
        "_new_person_address": "",
        "last_suggestion_error": "",
        "form_error": "",
        "form_success": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# =========================================================
# Distance Utility
# =========================================================
def haversine_miles(coord1, coord2):
    """Return great-circle distance between two (lat, lon) coordinates in miles."""
    lat1, lon1 = coord1
    lat2, lon2 = coord2

    radius_miles = 3958.7613

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_miles * c


# =========================================================
# ORS API Calls
# =========================================================
@st.cache_data(show_spinner=False)
def geocode_address_request(address, api_key):
    """Call ORS geocoding API and return (lat, lon) for the best result."""
    url = "https://api.openrouteservice.org/geocode/search"
    params = {
        "api_key": api_key,
        "text": address,
        "size": 1,
    }

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()

    features = data.get("features", [])
    if not features:
        raise ValueError(f"No geocoding result found for: {address}")

    coords = features[0]["geometry"]["coordinates"]
    lon, lat = coords[0], coords[1]
    return (lat, lon)


@st.cache_data(show_spinner=False)
def autocomplete_address_request(query, api_key):
    """Call ORS autocomplete API and return a list of suggestion dicts."""
    url = "https://api.openrouteservice.org/geocode/autocomplete"
    params = {
        "api_key": api_key,
        "text": query,
        "size": 5,
    }

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()

    suggestions = []
    seen_labels = set()

    for feature in data.get("features", []):
        properties = feature.get("properties", {})
        label = properties.get("label") or properties.get("name") or ""
        if not label or label in seen_labels:
            continue

        coords = feature.get("geometry", {}).get("coordinates", [])
        cached_coords = None
        if len(coords) >= 2:
            cached_coords = (coords[1], coords[0])

        suggestions.append({"label": label, "coords": cached_coords})
        seen_labels.add(label)

    return suggestions


def geocode_address(address, api_key):
    """Geocode an address with session-state caching."""
    normalized = address.strip()
    if not normalized:
        raise ValueError("Address is empty.")

    if normalized in st.session_state.geocode_cache:
        return st.session_state.geocode_cache[normalized]

    coords = geocode_address_request(normalized, api_key)
    st.session_state.geocode_cache[normalized] = coords
    return coords


def get_address_suggestions(query, api_key):
    """Fetch autocomplete suggestions and warm cache with returned coordinates."""
    normalized = query.strip()
    if len(normalized) < 1 or not api_key:
        return []

    suggestions = autocomplete_address_request(normalized, api_key)

    for suggestion in suggestions:
        if suggestion["coords"] and suggestion["label"] not in st.session_state.geocode_cache:
            st.session_state.geocode_cache[suggestion["label"]] = suggestion["coords"]

    return suggestions


def geocode_all_addresses(church_address, people, api_key):
    """Geocode church and all people addresses."""
    church_coords = geocode_address(church_address, api_key)

    enriched_people = []
    for person in people:
        coords = geocode_address(person["address"], api_key)
        enriched_person = dict(person)
        enriched_person["coords"] = coords
        enriched_people.append(enriched_person)

    return church_coords, enriched_people


# =========================================================
# Safe Widget State Helpers
# =========================================================
def sync_temp_to_perm(temp_key, perm_key):
    """Copy temporary widget value into permanent session-state key."""
    st.session_state[perm_key] = st.session_state.get(temp_key, "")


def load_perm_to_temp(perm_key, temp_key):
    """Load permanent session-state value into widget temp key."""
    st.session_state[temp_key] = st.session_state.get(perm_key, "")


def choose_suggestion(perm_key, temp_key, label):
    """Safely select a suggestion in a callback."""
    st.session_state[perm_key] = label
    st.session_state[temp_key] = label


# =========================================================
# Address Autocomplete UI
# =========================================================
def render_address_autocomplete(label, key_name, api_key, placeholder, help_text=None):
    """
    Render a text input plus clickable ORS suggestions.

    Uses:
      - permanent key: key_name
      - widget temp key: f"_{key_name}"
    """
    temp_key = f"_{key_name}"

    if temp_key not in st.session_state:
        load_perm_to_temp(key_name, temp_key)

    st.text_input(
        label,
        key=temp_key,
        placeholder=placeholder,
        help=help_text,
        on_change=sync_temp_to_perm,
        args=(temp_key, key_name),
    )

    query = st.session_state.get(temp_key, "").strip()

    if not api_key:
        st.caption("Add your ORS API key in the sidebar to enable address suggestions.")
        return query

    # Start suggesting from the very first typed character
    if len(query) < 1:
        st.caption("Start typing to see address suggestions.")
        return query

    try:
        suggestions = get_address_suggestions(query, api_key)
        st.session_state.last_suggestion_error = ""
    except requests.exceptions.RequestException:
        suggestions = []
        st.session_state.last_suggestion_error = "Address suggestions are temporarily unavailable."
    except Exception:
        suggestions = []
        st.session_state.last_suggestion_error = "Unable to load address suggestions right now."

    if st.session_state.last_suggestion_error:
        st.caption(f"⚠️ {st.session_state.last_suggestion_error}")
        return query

    if not suggestions:
        st.caption("No suggested matches yet. You can still use the address you typed.")
        return query

    st.caption("Suggested matches:")
    for idx, suggestion in enumerate(suggestions, start=1):
        st.button(
            f"{idx}. {suggestion['label']}",
            key=f"{key_name}_suggestion_{idx}",
            use_container_width=True,
            on_click=choose_suggestion,
            args=(key_name, temp_key, suggestion["label"]),
        )

    return st.session_state.get(key_name, query).strip()


# =========================================================
# Carpool Optimization
# =========================================================
def assign_passengers_to_drivers(drivers, passengers):
    """
    Phase 1:
    Greedily assign each passenger to the nearest driver with remaining capacity.
    """
    car_groups = []
    for driver in drivers:
        car_groups.append(
            {
                "driver": driver,
                "passengers": [],
                "remaining_capacity": driver["capacity"],
            }
        )

    unassigned = passengers.copy()

    while unassigned:
        best_pair = None

        for passenger in unassigned:
            for group in car_groups:
                if group["remaining_capacity"] <= 0:
                    continue

                distance = haversine_miles(passenger["coords"], group["driver"]["coords"])
                if best_pair is None or distance < best_pair[0]:
                    best_pair = (distance, passenger, group)

        if best_pair is None:
            break

        _, passenger, group = best_pair
        group["passengers"].append(passenger)
        group["remaining_capacity"] -= 1
        unassigned = [p for p in unassigned if p["id"] != passenger["id"]]

    return car_groups, unassigned


def order_pickups_for_group(driver, passengers, church_coords):
    """
    Phase 2:
    Order pickups using nearest-neighbor route ordering.
    Start at driver home, visit closest passenger repeatedly, end at church.
    """
    remaining = passengers.copy()
    ordered = []
    route_points = [driver["coords"]]
    current = driver["coords"]
    total_distance = 0.0

    while remaining:
        next_passenger = min(
            remaining,
            key=lambda p: haversine_miles(current, p["coords"]),
        )
        total_distance += haversine_miles(current, next_passenger["coords"])
        ordered.append(next_passenger)
        route_points.append(next_passenger["coords"])
        current = next_passenger["coords"]
        remaining = [p for p in remaining if p["id"] != next_passenger["id"]]

    total_distance += haversine_miles(current, church_coords)
    route_points.append(church_coords)

    return ordered, total_distance, route_points


def optimize_carpools(church_coords, people):
    """Run the full two-phase greedy optimization."""
    drivers = [p for p in people if p["has_car"]]
    passengers = [p for p in people if not p["has_car"]]

    if not drivers:
        return {
            "cars": [],
            "unassigned": passengers,
            "stats": {
                "cars_used": 0,
                "total_distance": 0.0,
                "people_transported": 0,
            },
        }

    car_groups, unassigned = assign_passengers_to_drivers(drivers, passengers)

    cars = []
    total_distance = 0.0
    people_transported = 0

    for group in car_groups:
        driver = group["driver"]
        ordered_passengers, route_distance, route_points = order_pickups_for_group(
            driver,
            group["passengers"],
            church_coords,
        )

        used_seats = len(ordered_passengers)
        total_distance += route_distance
        people_transported += 1 + used_seats

        cars.append(
            {
                "driver": driver,
                "ordered_passengers": ordered_passengers,
                "distance_miles": route_distance,
                "seats_used": used_seats,
                "capacity": driver["capacity"],
                "route_points": route_points,
            }
        )

    return {
        "cars": cars,
        "unassigned": unassigned,
        "stats": {
            "cars_used": len(cars),
            "total_distance": total_distance,
            "people_transported": people_transported,
        },
    }


# =========================================================
# CSV Export
# =========================================================
def export_results_csv(results, church_address):
    rows = []

    for car in results["cars"]:
        driver = car["driver"]
        passengers = car["ordered_passengers"]

        rows.append(
            {
                "driver_name": driver["name"],
                "driver_address": driver["address"],
                "church_address": church_address,
                "passenger_count": len(passengers),
                "passenger_names_in_pickup_order": " | ".join(p["name"] for p in passengers),
                "passenger_addresses_in_pickup_order": " | ".join(p["address"] for p in passengers),
                "seats_used": car["seats_used"],
                "capacity": car["capacity"],
                "estimated_distance_miles": round(car["distance_miles"], 2),
            }
        )

    for passenger in results["unassigned"]:
        rows.append(
            {
                "driver_name": "UNASSIGNED",
                "driver_address": "",
                "church_address": church_address,
                "passenger_count": 1,
                "passenger_names_in_pickup_order": passenger["name"],
                "passenger_addresses_in_pickup_order": passenger["address"],
                "seats_used": 0,
                "capacity": 0,
                "estimated_distance_miles": "",
            }
        )

    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8")


# =========================================================
# Display Helpers
# =========================================================
def build_people_table(people):
    rows = []
    for person in people:
        rows.append(
            {
                "Name": person["name"],
                "Address": person["address"],
                "Role": "🚗 Driver" if person["has_car"] else "🧍 Passenger",
                "Capacity": person["capacity"] if person["has_car"] else 0,
            }
        )
    return pd.DataFrame(rows)


def display_summary(results):
    stats = results["stats"]

    st.subheader("📊 Carpool Summary")
    col1, col2, col3 = st.columns(3)
    col1.metric("Cars Used", stats["cars_used"])
    col2.metric("Total Distance", f"{stats['total_distance']:.2f} miles")
    col3.metric("People Transported", stats["people_transported"])

    if results["unassigned"]:
        st.warning(
            "⚠️ Not enough available seats for everyone. Unassigned passengers: "
            + ", ".join(p["name"] for p in results["unassigned"])
        )

    st.subheader("🚗 Car Assignments")
    for idx, car in enumerate(results["cars"], start=1):
        driver = car["driver"]
        passengers = car["ordered_passengers"]

        with st.container(border=True):
            st.markdown(f"### Car {idx}: 🚗 {driver['name']}")
            st.write(f"**Driver address:** {driver['address']}")
            st.write(f"**Seats used:** {car['seats_used']} / {car['capacity']}")
            st.write(f"**Estimated distance:** {car['distance_miles']:.2f} miles")

            if passengers:
                st.write("**Pickup order:**")
                for stop_number, passenger in enumerate(passengers, start=1):
                    st.write(f"{stop_number}. 🧍 {passenger['name']} — {passenger['address']}")
            else:
                st.write("**Pickup order:** No passengers assigned. Driver goes directly to church.")


def build_map(church_coords, church_address, results):
    all_points = [church_coords]
    for car in results["cars"]:
        all_points.extend(car["route_points"])

    center_lat = sum(point[0] for point in all_points) / len(all_points)
    center_lon = sum(point[1] for point in all_points) / len(all_points)

    trip_map = folium.Map(location=[center_lat, center_lon], zoom_start=11)

    folium.Marker(
        location=list(church_coords),
        popup=f"⛪ Church<br>{church_address}",
        tooltip="⛪ Church",
        icon=folium.Icon(color="red", icon="home"),
    ).add_to(trip_map)

    route_colors = [
        "blue",
        "green",
        "purple",
        "orange",
        "darkred",
        "cadetblue",
        "darkgreen",
        "pink",
        "gray",
        "black",
    ]

    for idx, car in enumerate(results["cars"]):
        color = route_colors[idx % len(route_colors)]
        driver = car["driver"]

        folium.Marker(
            location=list(driver["coords"]),
            popup=f"🚗 {driver['name']}<br>{driver['address']}",
            tooltip=f"🚗 {driver['name']}",
            icon=folium.Icon(color=color, icon="user"),
        ).add_to(trip_map)

        for passenger in car["ordered_passengers"]:
            folium.Marker(
                location=list(passenger["coords"]),
                popup=f"🧍 {passenger['name']}<br>{passenger['address']}",
                tooltip=f"🧍 {passenger['name']}",
                icon=folium.Icon(color="lightgray", icon="info-sign"),
            ).add_to(trip_map)

        folium.PolyLine(
            locations=[list(point) for point in car["route_points"]],
            color=color,
            weight=4,
            opacity=0.8,
            tooltip=f"Route for {driver['name']}",
        ).add_to(trip_map)

    return trip_map


# =========================================================
# Actions
# =========================================================
def clear_new_person_inputs():
    """Clear add-person widgets safely when called from a callback."""
    st.session_state.new_person_name = ""
    st.session_state.new_person_has_car = False
    st.session_state.new_person_capacity = 3
    st.session_state.new_person_address = ""
    st.session_state._new_person_address = ""


def add_person_callback():
    """Add a person using current widget values, then clear the widgets."""
    name = st.session_state.new_person_name.strip()
    address = st.session_state.new_person_address.strip()
    has_car = st.session_state.new_person_has_car
    capacity = int(st.session_state.new_person_capacity) if has_car else 0

    st.session_state.form_error = ""
    st.session_state.form_success = ""

    if not name or not address:
        st.session_state.form_error = "Please provide both a name and a full address."
        return

    if has_car and capacity < 1:
        st.session_state.form_error = "A driver must have at least 1 passenger seat."
        return

    st.session_state.person_counter += 1
    st.session_state.people.append(
        {
            "id": st.session_state.person_counter,
            "name": name,
            "address": address,
            "has_car": has_car,
            "capacity": capacity,
        }
    )

    st.session_state.carpool_results = None
    clear_new_person_inputs()
    st.session_state.form_success = f"Added {name}."


def remove_person(person_id):
    st.session_state.people = [p for p in st.session_state.people if p["id"] != person_id]
    st.session_state.carpool_results = None


def reset_all():
    """Reset the whole app safely when called from a callback."""
    st.session_state.church_address = ""
    st.session_state._church_address = ""
    st.session_state.people = []
    st.session_state.carpool_results = None
    st.session_state.person_counter = 0
    st.session_state.geocode_cache = {}
    st.session_state.last_suggestion_error = ""
    st.session_state.form_error = ""
    st.session_state.form_success = ""
    clear_new_person_inputs()


def generate_carpools():
    church_address = st.session_state.church_address.strip()
    api_key = st.session_state.ors_api_key.strip()
    people = st.session_state.people

    if not api_key:
        st.warning("Please paste your OpenRouteService API key in the sidebar.")
        return

    if not church_address:
        st.warning("Please enter the church address.")
        return

    if not people:
        st.warning("Please add at least one person.")
        return

    drivers = [p for p in people if p["has_car"]]
    if not drivers:
        st.warning("No drivers found. Add at least one person with a car.")
        return

    try:
        with st.spinner("📍 Geocoding addresses..."):
            church_coords, enriched_people = geocode_all_addresses(church_address, people, api_key)

        with st.spinner("🚗 Optimizing carpools..."):
            results = optimize_carpools(church_coords, enriched_people)
            results["church_coords"] = church_coords
            results["church_address"] = church_address
            st.session_state.carpool_results = results

    except requests.exceptions.RequestException as exc:
        st.error(f"Network/API error while geocoding: {exc}")
    except ValueError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"Unexpected error: {exc}")


# =========================================================
# Main App
# =========================================================
def main():
    init_session_state()

    st.title("⛪ Church Carpool Optimizer")
    st.caption("Automatically group your congregation into efficient carpools.")

    with st.sidebar:
        st.header("🔑 OpenRouteService")
        st.text_input(
            "Paste your ORS API key",
            key="ors_api_key",
            type="password",
            help="Used only for geocoding and autocomplete.",
        )
        st.markdown("[Get a free API key →](https://openrouteservice.org/sign-up/)")
        st.info("Autocomplete and geocoding use ORS. Route ordering uses Haversine distance only.")

    api_key = st.session_state.ors_api_key

    # Church input
    st.subheader("⛪ Church destination")
    render_address_autocomplete(
        label="Church address",
        key_name="church_address",
        api_key=api_key,
        placeholder="123 Main St, Springfield, IL 62701",
        help_text="Start typing and choose one of the suggested addresses.",
    )

    # Add person section
    with st.container(border=True):
        st.subheader("➕ Add a congregation member")

        col1, col2 = st.columns([1, 2])

        with col1:
            st.text_input("Name", key="new_person_name", placeholder="Jane Smith")
            st.toggle("Has a car? 🚗", key="new_person_has_car")
            st.number_input(
                "Passenger seats",
                min_value=1,
                max_value=20,
                step=1,
                key="new_person_capacity",
                disabled=not st.session_state.new_person_has_car,
            )

        with col2:
            render_address_autocomplete(
                label="Home address",
                key_name="new_person_address",
                api_key=api_key,
                placeholder="456 Oak Ave, Springfield, IL 62704",
                help_text="Suggestions appear as you type.",
            )

        st.button("➕ Add Person", use_container_width=True, on_click=add_person_callback)

        if st.session_state.form_error:
            st.error(st.session_state.form_error)

        if st.session_state.form_success:
            st.success(st.session_state.form_success)

    # People list
    st.subheader("👥 Current People")
    people = st.session_state.people

    if people:
        st.dataframe(build_people_table(people), use_container_width=True, hide_index=True)

        st.write("Remove an entry:")
        cols = st.columns(min(len(people), 4))
        for idx, person in enumerate(people):
            with cols[idx % len(cols)]:
                if st.button(f"❌ {person['name']}", key=f"remove_{person['id']}"):
                    remove_person(person["id"])
                    st.rerun()

        drivers_count = sum(1 for p in people if p["has_car"])
        passengers_count = sum(1 for p in people if not p["has_car"])
        total_seats = sum(p["capacity"] for p in people if p["has_car"])

        st.write(
            f"🚗 Drivers: **{drivers_count}** | "
            f"🧍 Passengers: **{passengers_count}** | "
            f"💺 Total Seats: **{total_seats}**"
        )
    else:
        st.info("Add people to get started.")

    action_col1, action_col2 = st.columns(2)
    with action_col1:
        if st.button("🚗 Generate Carpools", type="primary", use_container_width=True):
            generate_carpools()

    with action_col2:
        st.button("🔄 Reset", use_container_width=True, on_click=reset_all)

    # Results
    results = st.session_state.carpool_results
    if results:
        st.markdown("---")
        display_summary(results)

        st.subheader("🗺️ Pickup Map")
        trip_map = build_map(results["church_coords"], results["church_address"], results)
        st_folium(trip_map, width=None, height=550)

        csv_bytes = export_results_csv(results, results["church_address"])
        st.download_button(
            "⬇️ Export Assignments CSV",
            data=csv_bytes,
            file_name="church_carpool_assignments.csv",
            mime="text/csv",
        )

        preview_rows = []
        for car in results["cars"]:
            preview_rows.append(
                {
                    "Driver": car["driver"]["name"],
                    "Driver Address": car["driver"]["address"],
                    "Passengers": ", ".join(p["name"] for p in car["ordered_passengers"])
                    if car["ordered_passengers"] else "",
                    "Distance (miles)": round(car["distance_miles"], 2),
                    "Seats Used": f"{car['seats_used']} / {car['capacity']}",
                }
            )

        if preview_rows:
            st.subheader("📄 Results Table")
            st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)

    if len(people) == 1:
        only_person = people[0]
        if only_person["has_car"]:
            st.info("With one driver, the route is just that person going directly to church.")
        else:
            st.info("With one passenger and no driver, the app will warn that no car is available.")


if __name__ == "__main__":
    main()
