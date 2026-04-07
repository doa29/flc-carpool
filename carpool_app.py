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


# ---------- Page setup ----------
st.set_page_config(page_title="Church Carpool Optimizer", page_icon="⛪", layout="wide")


# ---------- Session state initialization ----------
def init_session_state():
    """Initialize all session state keys used by the app."""
    defaults = {
        "ors_api_key": "",
        "church_address": "",
        "people": [],
        "geocode_cache": {},
        "carpool_results": None,
        "warnings": [],
        "last_error": None,
        "last_suggestion_error": None,
        "person_counter": 0,
        "new_person_name": "",
        "new_person_has_car": False,
        "new_person_capacity": 3,
        "new_person_address": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ---------- Utility functions ----------
def haversine_miles(coord1, coord2):
    """Return the great-circle distance between two (lat, lon) points in miles."""
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


@st.cache_data(show_spinner=False)
def geocode_address_request(address, api_key):
    """Call the ORS geocoding API once and return (lat, lon) for the best match."""
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

    coordinates = features[0]["geometry"]["coordinates"]
    lon, lat = coordinates[0], coordinates[1]
    return (lat, lon)


@st.cache_data(show_spinner=False)
def autocomplete_address_request(query, api_key):
    """Call the ORS autocomplete geocoder and return a short list of suggestions."""
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
    """Geocode an address with a session-state cache so reruns do not repeat the request."""
    normalized = address.strip()
    if not normalized:
        raise ValueError("Address is empty.")

    cache = st.session_state.geocode_cache
    if normalized in cache:
        return cache[normalized]

    coords = geocode_address_request(normalized, api_key)
    cache[normalized] = coords
    st.session_state.geocode_cache = cache
    return coords


def get_address_suggestions(query, api_key):
    """Fetch autocomplete suggestions and warm the geocode cache for selected labels."""
    normalized = query.strip()
    if len(normalized) < 3 or not api_key:
        return []

    suggestions = autocomplete_address_request(normalized, api_key)

    cache = st.session_state.geocode_cache
    for suggestion in suggestions:
        if suggestion["coords"] and suggestion["label"] not in cache:
            cache[suggestion["label"]] = suggestion["coords"]
    st.session_state.geocode_cache = cache
    return suggestions


def geocode_all_addresses(church_address, people, api_key):
    """Geocode the church and all people. Returns a tuple of (church_coords, enriched_people)."""
    church_coords = geocode_address(church_address, api_key)

    enriched_people = []
    for person in people:
        coords = geocode_address(person["address"], api_key)
        enriched_person = dict(person)
        enriched_person["coords"] = coords
        enriched_people.append(enriched_person)

    return church_coords, enriched_people


def build_people_table(people):
    """Create a dataframe for the running list of people."""
    if not people:
        return pd.DataFrame(columns=["Name", "Address", "Role", "Capacity"])

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


def render_address_autocomplete(label, key_name, api_key, placeholder, icon=None, help_text=None):
    """Render a text box plus clickable ORS address suggestions beneath it."""
    current_value = st.text_input(
        label,
        key=key_name,
        placeholder=placeholder,
        help=help_text,
        icon=icon,
    )

    query = current_value.strip()
    if not api_key:
        st.caption("Add your ORS API key in the sidebar to enable address suggestions.")
        return query

    if len(query) < 3:
        st.caption("Start typing at least 3 characters to see address suggestions.")
        return query

    try:
        suggestions = get_address_suggestions(query, api_key)
        st.session_state.last_suggestion_error = None
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
        button_key = f"{key_name}_suggestion_{idx}"
        if st.button(f"{idx}. {suggestion['label']}", key=button_key, use_container_width=True):
            st.session_state[key_name] = suggestion["label"]
            st.rerun()

    return st.session_state.get(key_name, query).strip()


# ---------- Optimization functions ----------
def assign_passengers_to_drivers(drivers, passengers):
    """
    Phase 1:
    Greedily assign each passenger to the nearest driver with remaining capacity.
    Returns (car_groups, unassigned_passengers).
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

    # Repeatedly select the globally closest passenger-driver pair where capacity remains.
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
    Use nearest-neighbor route ordering from driver home through passengers, ending at church.
    Returns (ordered_passengers, total_distance_miles, route_points).
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
        leg_distance = haversine_miles(current, next_passenger["coords"])
        total_distance += leg_distance
        ordered.append(next_passenger)
        route_points.append(next_passenger["coords"])
        current = next_passenger["coords"]
        remaining = [p for p in remaining if p["id"] != next_passenger["id"]]

    # End the route at the church.
    total_distance += haversine_miles(current, church_coords)
    route_points.append(church_coords)

    return ordered, total_distance, route_points


def optimize_carpools(church_coords, people):
    """Run the two-phase greedy carpool optimizer."""
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
                "total_people": len(people),
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

        # A car is counted as used if the driver is making the trip.
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

    cars_used = len(cars)

    return {
        "cars": cars,
        "unassigned": unassigned,
        "stats": {
            "cars_used": cars_used,
            "total_distance": total_distance,
            "people_transported": people_transported,
            "total_people": len(people),
        },
    }


def export_results_csv(results, church_address):
    """Create a CSV export for the current carpool assignments."""
    rows = []
    for car in results["cars"]:
        driver = car["driver"]
        passengers = car["ordered_passengers"]
        passenger_names = " | ".join(p["name"] for p in passengers) if passengers else ""
        passenger_addresses = " | ".join(p["address"] for p in passengers) if passengers else ""

        rows.append(
            {
                "driver_name": driver["name"],
                "driver_address": driver["address"],
                "church_address": church_address,
                "passenger_count": len(passengers),
                "passenger_names_in_pickup_order": passenger_names,
                "passenger_addresses_in_pickup_order": passenger_addresses,
                "seats_used": car["seats_used"],
                "capacity": car["capacity"],
                "estimated_distance_miles": round(car["distance_miles"], 2),
            }
        )

    if results["unassigned"]:
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

    df = pd.DataFrame(rows)
    return df.to_csv(index=False).encode("utf-8")


# ---------- Display functions ----------
def display_summary(results):
    """Render high-level stats and individual car summary cards."""
    stats = results["stats"]

    st.subheader("📊 Carpool Summary")
    col1, col2, col3 = st.columns(3)
    col1.metric("Cars Used", stats["cars_used"])
    col2.metric("Total Distance", f"{stats['total_distance']:.2f} miles")
    col3.metric("People Transported", stats["people_transported"])

    if results["unassigned"]:
        st.warning(
            "⚠️ Overflow warning: not enough driver seats for everyone. "
            f"Unassigned passengers: {', '.join(p['name'] for p in results['unassigned'])}"
        )

    if not results["cars"]:
        return

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
    """Build a folium map showing the church, people, and each car's route."""
    all_points = [church_coords]
    for car in results["cars"]:
        all_points.extend(car["route_points"])

    center_lat = sum(point[0] for point in all_points) / len(all_points)
    center_lon = sum(point[1] for point in all_points) / len(all_points)

    trip_map = folium.Map(location=[center_lat, center_lon], zoom_start=11)

    # Church marker.
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
        passengers = car["ordered_passengers"]

        # Driver marker.
        folium.Marker(
            location=list(driver["coords"]),
            popup=f"🚗 {driver['name']}<br>{driver['address']}",
            tooltip=f"🚗 {driver['name']}",
            icon=folium.Icon(color=color, icon="user"),
        ).add_to(trip_map)

        # Passenger markers.
        for passenger in passengers:
            folium.Marker(
                location=list(passenger["coords"]),
                popup=f"🧍 {passenger['name']}<br>{passenger['address']}",
                tooltip=f"🧍 {passenger['name']}",
                icon=folium.Icon(color="lightgray", icon="info-sign"),
            ).add_to(trip_map)

        # Route polyline.
        folium.PolyLine(
            locations=[list(point) for point in car["route_points"]],
            color=color,
            weight=4,
            opacity=0.8,
            tooltip=f"Route for {driver['name']}",
        ).add_to(trip_map)

    return trip_map


# ---------- Action handlers ----------
def clear_new_person_inputs():
    """Reset the add-person inputs after a successful add."""
    st.session_state.new_person_name = ""
    st.session_state.new_person_has_car = False
    st.session_state.new_person_capacity = 3
    st.session_state.new_person_address = ""


def add_person(name, address, has_car, capacity):
    """Add a person to the session-state list."""
    clean_name = name.strip()
    clean_address = address.strip()

    if not clean_name or not clean_address:
        st.error("Please provide both a name and a full address.")
        return False

    if has_car and capacity < 0:
        st.error("Capacity cannot be negative.")
        return False

    st.session_state.person_counter += 1
    st.session_state.people.append(
        {
            "id": st.session_state.person_counter,
            "name": clean_name,
            "address": clean_address,
            "has_car": has_car,
            "capacity": int(capacity) if has_car else 0,
        }
    )
    st.session_state.carpool_results = None
    clear_new_person_inputs()
    return True


def remove_person(person_id):
    """Remove a person from the session-state list."""
    st.session_state.people = [p for p in st.session_state.people if p["id"] != person_id]
    st.session_state.carpool_results = None


def reset_all():
    """Clear user-entered data and cached results."""
    st.session_state.church_address = ""
    st.session_state.people = []
    st.session_state.carpool_results = None
    st.session_state.warnings = []
    st.session_state.last_error = None
    st.session_state.last_suggestion_error = None
    st.session_state.person_counter = 0
    st.session_state.geocode_cache = {}
    clear_new_person_inputs()


def generate_carpools():
    """Validate input, geocode addresses, run optimization, and store results."""
    church_address = st.session_state.church_address.strip()
    api_key = st.session_state.ors_api_key.strip()
    people = st.session_state.people

    if not api_key:
        st.warning("Please paste your OpenRouteService API key in the sidebar before generating carpools.")
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
        with st.spinner("📍 Geocoding addresses with OpenRouteService..."):
            church_coords, enriched_people = geocode_all_addresses(church_address, people, api_key)

        results = optimize_carpools(church_coords, enriched_people)
        results["church_coords"] = church_coords
        results["church_address"] = church_address
        st.session_state.carpool_results = results
        st.session_state.last_error = None
    except requests.exceptions.RequestException as exc:
        st.session_state.last_error = f"Network/API error while geocoding: {exc}"
        st.error(st.session_state.last_error)
    except ValueError as exc:
        st.session_state.last_error = str(exc)
        st.error(st.session_state.last_error)
    except Exception as exc:
        st.session_state.last_error = f"Unexpected error: {exc}"
        st.error(st.session_state.last_error)


# ---------- Main UI ----------
def main():
    init_session_state()

    st.title("⛪ Church Carpool Optimizer")
    st.caption(
        "Build friendly carpools that reduce driving distance using free ORS geocoding and a greedy spatial optimizer."
    )

    # Sidebar for API key.
    with st.sidebar:
        st.header("🔑 OpenRouteService")
        api_key_input = st.text_input(
            "Paste your ORS API key",
            value=st.session_state.ors_api_key,
            type="password",
            help="Used only for geocoding church and home addresses.",
        )
        st.session_state.ors_api_key = api_key_input.strip()
        st.markdown("Need a free key? [Sign up at OpenRouteService](https://openrouteservice.org/sign-up/)")
        st.info("Only geocoding uses ORS. Route ordering uses Haversine math only.")

    api_key = st.session_state.ors_api_key

    # Top inputs.
    st.subheader("⛪ Church destination")
    render_address_autocomplete(
        label="Church address",
        key_name="church_address",
        api_key=api_key,
        placeholder="123 Main St, Springfield, IL 62701",
        icon="⛪",
        help_text="Start typing and choose one of the suggested addresses.",
    )

    # Add-person area.
    with st.container(border=True):
        st.subheader("➕ Add a congregation member")
        col1, col2 = st.columns([1, 2])
        with col1:
            st.text_input("Name", key="new_person_name", placeholder="Jane Smith")
            st.toggle("Has a car?", key="new_person_has_car")
            st.number_input(
                "If yes, how many passengers can they carry?",
                min_value=0,
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
                icon="🏠",
                help_text="Pick a suggested address or keep the one you typed.",
            )

        if st.button("Add Person", type="secondary"):
            added = add_person(
                st.session_state.new_person_name,
                st.session_state.new_person_address,
                st.session_state.new_person_has_car,
                st.session_state.new_person_capacity,
            )
            if added:
                st.rerun()

    # Running list of people.
    st.subheader("👥 Current People")
    people = st.session_state.people
    if people:
        st.dataframe(build_people_table(people), use_container_width=True, hide_index=True)
        st.write("Remove an entry:")
        remove_cols = st.columns(min(len(people), 4) or 1)
        for idx, person in enumerate(people):
            with remove_cols[idx % len(remove_cols)]:
                if st.button(f"❌ {person['name']}", key=f"remove_{person['id']}"):
                    remove_person(person["id"])
                    st.rerun()
    else:
        st.info("No people added yet.")

    action_col1, action_col2, action_col3 = st.columns([1, 1, 2])
    with action_col1:
        if st.button("🚗 Generate Carpools", type="primary", use_container_width=True):
            generate_carpools()
    with action_col2:
        if st.button("🔄 Reset", use_container_width=True):
            reset_all()
            st.rerun()

    # Results section.
    results = st.session_state.carpool_results
    if results:
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
            use_container_width=False,
        )

        # Optional plain table view for quick scanning/export verification.
        export_preview = []
        for car in results["cars"]:
            export_preview.append(
                {
                    "Driver": car["driver"]["name"],
                    "Driver Address": car["driver"]["address"],
                    "Passengers (ordered)": ", ".join(p["name"] for p in car["ordered_passengers"])
                    if car["ordered_passengers"]
                    else "",
                    "Distance (miles)": round(car["distance_miles"], 2),
                    "Seats Used": f"{car['seats_used']} / {car['capacity']}",
                }
            )
        if export_preview:
            st.subheader("📄 Results Table")
            st.dataframe(pd.DataFrame(export_preview), use_container_width=True, hide_index=True)

    # Helpful edge-case note.
    if len(people) == 1:
        only_person = people[0]
        if only_person["has_car"]:
            st.info("With one driver, the trip will simply be that person driving directly to church.")
        else:
            st.info("With one passenger and no driver, the app will warn that no car is available.")


if __name__ == "__main__":
    main()
