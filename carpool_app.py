# pip install streamlit requests folium streamlit-folium pandas

import streamlit as st
import requests
import math
import pandas as pd
import folium
from streamlit_folium import st_folium

# ─── Haversine Distance ───────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8  # miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ─── Geocoding ────────────────────────────────────────────────────────────────

def geocode_address(address, api_key):
    if address in st.session_state.geocache:
        return st.session_state.geocache[address]
    url = "https://api.openrouteservice.org/geocode/search"
    params = {"api_key": api_key, "text": address, "size": 1}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            return None
        coords = features[0]["geometry"]["coordinates"]
        result = (coords[1], coords[0])  # (lat, lon)
        st.session_state.geocache[address] = result
        return result
    except Exception as e:
        st.error(f"Geocoding error for '{address}': {e}")
        return None

# ─── Route Ordering (nearest-neighbor TSP) ───────────────────────────────────

def order_route(driver, passengers, church_coords):
    ordered = []
    current = driver["coords"]
    remaining = list(passengers)
    while remaining:
        nearest = min(remaining, key=lambda p: haversine(*current, *p["coords"]))
        ordered.append(nearest)
        current = nearest["coords"]
        remaining.remove(nearest)
    total_dist = 0
    cur = driver["coords"]
    for p in ordered:
        total_dist += haversine(*cur, *p["coords"])
        cur = p["coords"]
    total_dist += haversine(*cur, *church_coords)
    return ordered, round(total_dist, 2)

# ─── Clustering Algorithm ─────────────────────────────────────────────────────

def assign_carpools(people, church_coords):
    drivers = [p for p in people if p["has_car"] and p["coords"]]
    passengers = [p for p in people if not p["has_car"] and p["coords"]]

    assignments = {d["name"]: {"driver": d, "passengers": [], "capacity": d["seats"]} for d in drivers}
    unassigned = list(passengers)

    # Greedy: for each unassigned passenger, find nearest driver with capacity
    for _ in range(len(unassigned)):
        if not unassigned:
            break
        best_passenger = None
        best_driver_name = None
        best_dist = float("inf")

        for p in unassigned:
            for d in drivers:
                car = assignments[d["name"]]
                if len(car["passengers"]) >= car["capacity"]:
                    continue
                dist = haversine(*p["coords"], *d["coords"])
                if dist < best_dist:
                    best_dist = dist
                    best_passenger = p
                    best_driver_name = d["name"]

        if best_passenger is None:
            break
        assignments[best_driver_name]["passengers"].append(best_passenger)
        unassigned.remove(best_passenger)

    # Order each route
    results = []
    for name, car in assignments.items():
        ordered_passengers, total_dist = order_route(car["driver"], car["passengers"], church_coords)
        results.append({
            "driver": car["driver"],
            "passengers": ordered_passengers,
            "total_dist": total_dist,
            "capacity": car["capacity"],
        })

    return results, unassigned

# ─── Map Builder ─────────────────────────────────────────────────────────────

COLORS = ["red", "blue", "green", "purple", "orange", "darkred", "cadetblue", "darkgreen"]

def build_map(carpools, church_coords, church_address):
    m = folium.Map(location=church_coords, zoom_start=12, tiles="CartoDB positron")
    folium.Marker(
        church_coords,
        tooltip=f"⛪ {church_address}",
        icon=folium.Icon(color="black", icon="home", prefix="fa")
    ).add_to(m)

    for i, car in enumerate(carpools):
        color = COLORS[i % len(COLORS)]
        driver = car["driver"]
        route_points = [driver["coords"]] + [p["coords"] for p in car["passengers"]] + [church_coords]
        folium.PolyLine(route_points, color=color, weight=2.5, opacity=0.7).add_to(m)
        folium.Marker(
            driver["coords"],
            tooltip=f"🚗 {driver['name']} (driver)",
            icon=folium.Icon(color=color, icon="car", prefix="fa")
        ).add_to(m)
        for p in car["passengers"]:
            folium.Marker(
                p["coords"],
                tooltip=f"🧍 {p['name']}",
                icon=folium.Icon(color=color, icon="user", prefix="fa")
            ).add_to(m)
    return m

# ─── Session State Init ───────────────────────────────────────────────────────

def init_state():
    defaults = {
        "people": [],
        "carpools": None,
        "overflow": [],
        "geocache": {},
        "church_coords": None,
        "church_address": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

# ─── CSV Export ───────────────────────────────────────────────────────────────

def build_csv(carpools, overflow):
    rows = []
    for car in carpools:
        d = car["driver"]
        rows.append({"Role": "Driver", "Name": d["name"], "Address": d["address"],
                     "Car": d["name"], "Pickup Order": 0, "Route Distance (mi)": car["total_dist"]})
        for idx, p in enumerate(car["passengers"], 1):
            rows.append({"Role": "Passenger", "Name": p["name"], "Address": p["address"],
                         "Car": d["name"], "Pickup Order": idx, "Route Distance (mi)": ""})
    for p in overflow:
        rows.append({"Role": "Unassigned", "Name": p["name"], "Address": p["address"],
                     "Car": "", "Pickup Order": "", "Route Distance (mi)": ""})
    return pd.DataFrame(rows).to_csv(index=False)

# ─── Main App ─────────────────────────────────────────────────────────────────

def main():
    init_state()

    st.set_page_config(page_title="Church Carpool Optimizer", page_icon="⛪", layout="wide")

    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@300;400;500&display=swap');
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    h1, h2, h3 { font-family: 'DM Serif Display', serif; }
    .car-card {
        background: white;
        border-radius: 12px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 1rem;
        border-left: 5px solid #5C6BC0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    }
    .stat-pill {
        display: inline-block;
        background: #EEF2FF;
        color: #3949AB;
        border-radius: 20px;
        padding: 3px 12px;
        font-size: 0.82rem;
        font-weight: 500;
        margin-right: 6px;
    }
    </style>
    """, unsafe_allow_html=True)

    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("## ⛪ Carpool Optimizer")
        st.markdown("---")
        api_key = st.text_input("🔑 ORS API Key", type="password",
                                help="Free key from openrouteservice.org")
        st.markdown("[Get a free API key →](https://openrouteservice.org/sign-up/)")
        st.markdown("---")
        st.markdown("**Church Address**")
        church_input = st.text_input("Address", placeholder="123 Main St, City, State",
                                     label_visibility="collapsed")
        st.markdown("---")
        st.markdown("**Add a Person**")
        name = st.text_input("Name")
        address = st.text_input("Home Address", placeholder="456 Oak Ave, City, State")
        has_car = st.toggle("Has a car 🚗")
        seats = 3
        if has_car:
            seats = st.number_input("Passenger seats", min_value=1, max_value=10, value=3)

        if st.button("➕ Add Person", use_container_width=True):
            if name and address:
                st.session_state.people.append({
                    "name": name, "address": address,
                    "has_car": has_car, "seats": int(seats), "coords": None
                })
                st.success(f"Added {name}")
                st.rerun()
            else:
                st.warning("Please enter a name and address.")

        if st.button("🗑️ Reset Everything", use_container_width=True):
            for k in ["people", "carpools", "overflow", "geocache", "church_coords", "church_address"]:
                st.session_state[k] = [] if k in ["people","overflow"] else None if "coords" in k or k=="carpools" else {}  if k=="geocache" else ""
            st.rerun()

    # ── Main Area ─────────────────────────────────────────────────────────────
    st.title("⛪ Church Carpool Optimizer")
    st.caption("Automatically group your congregation into efficient carpools.")

    # People list
    if st.session_state.people:
        st.markdown("### 👥 People")
        cols = st.columns([3, 3, 1, 1, 0.7])
        cols[0].markdown("**Name**")
        cols[1].markdown("**Address**")
        cols[2].markdown("**Car?**")
        cols[3].markdown("**Seats**")

        to_remove = None
        for i, p in enumerate(st.session_state.people):
            c = st.columns([3, 3, 1, 1, 0.7])
            c[0].write(p["name"])
            c[1].write(p["address"])
            c[2].write("🚗" if p["has_car"] else "🧍")
            c[3].write(p["seats"] if p["has_car"] else "—")
            if c[4].button("✕", key=f"rm_{i}"):
                to_remove = i
        if to_remove is not None:
            st.session_state.people.pop(to_remove)
            st.rerun()

        drivers_count = sum(1 for p in st.session_state.people if p["has_car"])
        passengers_count = sum(1 for p in st.session_state.people if not p["has_car"])
        total_seats = sum(p["seats"] for p in st.session_state.people if p["has_car"])
        st.markdown(f"""
        <span class='stat-pill'>🚗 {drivers_count} drivers</span>
        <span class='stat-pill'>🧍 {passengers_count} passengers</span>
        <span class='stat-pill'>💺 {total_seats} total seats</span>
        """, unsafe_allow_html=True)
        st.markdown("")

        if st.button("🚀 Generate Carpools", type="primary", use_container_width=True):
            if not api_key:
                st.error("Please enter your ORS API key in the sidebar.")
            elif not church_input:
                st.error("Please enter the church address in the sidebar.")
            else:
                with st.spinner("Geocoding addresses..."):
                    st.session_state.church_address = church_input
                    church_coords = geocode_address(church_input, api_key)
                    if not church_coords:
                        st.error("Could not geocode the church address. Check it and try again.")
                        st.stop()
                    st.session_state.church_coords = church_coords

                    for p in st.session_state.people:
                        coords = geocode_address(p["address"], api_key)
                        p["coords"] = coords

                    failed = [p["name"] for p in st.session_state.people if not p["coords"]]
                    if failed:
                        st.warning(f"Could not geocode: {', '.join(failed)}. They will be skipped.")

                with st.spinner("Optimizing carpools..."):
                    carpools, overflow = assign_carpools(
                        st.session_state.people, st.session_state.church_coords
                    )
                    st.session_state.carpools = carpools
                    st.session_state.overflow = overflow
                st.rerun()

    else:
        st.info("👈 Add people in the sidebar to get started.")

    # ── Results ───────────────────────────────────────────────────────────────
    if st.session_state.carpools is not None:
        carpools = st.session_state.carpools
        overflow = st.session_state.overflow

        st.markdown("---")
        st.markdown("### 🗺️ Carpool Map")
        m = build_map(carpools, st.session_state.church_coords, st.session_state.church_address)
        st_folium(m, width="100%", height=450)

        st.markdown("### 🚗 Carpool Assignments")

        # Summary stats
        total_dist = sum(c["total_dist"] for c in carpools)
        total_passengers = sum(len(c["passengers"]) for c in carpools)
        col1, col2, col3 = st.columns(3)
        col1.metric("Cars", len(carpools))
        col2.metric("People Transported", total_passengers + len(carpools))
        col3.metric("Total Distance", f"{round(total_dist, 1)} mi")

        BORDER_COLORS = ["#5C6BC0","#26A69A","#EF5350","#AB47BC","#FFA726","#42A5F5","#66BB6A","#EC407A"]
        for i, car in enumerate(carpools):
            d = car["driver"]
            color = BORDER_COLORS[i % len(BORDER_COLORS)]
            seats_used = len(car["passengers"])
            st.markdown(f"""
            <div class="car-card" style="border-left-color:{color}">
            <strong style="font-size:1.05rem">🚗 {d['name']}</strong>
            &nbsp;<span class='stat-pill'>{seats_used}/{car['capacity']} seats</span>
            <span class='stat-pill'>{car['total_dist']} mi</span><br>
            <small style="color:#666">{d['address']}</small>
            </div>
            """, unsafe_allow_html=True)

            if car["passengers"]:
                for idx, p in enumerate(car["passengers"], 1):
                    st.markdown(f"&nbsp;&nbsp;&nbsp;**{idx}.** 🧍 {p['name']} — {p['address']}")
            else:
                st.markdown("&nbsp;&nbsp;&nbsp;_No passengers assigned_")
            st.markdown("&nbsp;&nbsp;&nbsp;➡️ ⛪ Church")
            st.markdown("")

        if overflow:
            st.warning(f"⚠️ {len(overflow)} person(s) could not be assigned (not enough seats): " +
                       ", ".join(p["name"] for p in overflow))

        csv = build_csv(carpools, overflow)
        st.download_button("⬇️ Export CSV", csv, "carpools.csv", "text/csv", use_container_width=True)

if __name__ == "__main__":
    main()
