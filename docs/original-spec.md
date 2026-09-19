# 🚲 Avelo Route Optimizer

A real-time bike-share routing system that minimizes the risk of exceeding ride-duration limits using constraint-based pathfinding and delay-aware modeling.

---

## 📌 Overview

Bike-share systems like àVélo impose strict ride-duration limits (e.g. 30 minutes per trip segment). Exceeding these limits results in additional charges.

Traditional navigation tools optimize for shortest time or distance but **ignore these constraints**, often leading to infeasible routes.

This project solves that problem by:

- Splitting trips into valid segments  
- Ensuring each segment respects time limits  
- Incorporating real-world delays (e.g. traffic lights)  
- Using real-time station availability data  

---

## ⚙️ Core Concept

Instead of treating a trip as a single route:

```
Start → Destination
```

This system models it as:

```
Start → Station A → Station B → Destination
```

At each station:
- the bike is docked  
- a new bike is unlocked  
- the ride timer resets  

---

## 🎯 Objective

Given:
- a start location  
- a destination  
- a maximum ride duration (e.g. 30 minutes)  
- real-time station data  

Find:
> A sequence of stations such that each segment is feasible and minimizes total travel risk.

---

## 🧠 Key Features

### 🔄 Real-Time Data Integration
- Uses GBFS (General Bikeshare Feed Specification)
- Fetches:
  - station locations  
  - available bikes  
  - available docks  

---

### ⏱️ Time Estimation Model

Each segment is modeled as:

```
Total Time =
    cycling time
  + traffic light delay
  + docking overhead
  + safety buffer
```

---

### 🚦 Traffic Light Modeling

- Estimates number of intersections  
- Adds delay per traffic light  
- Produces more realistic travel times  

---

### 🔀 Multi-Hop Routing

Finds routes like:

```
Start → Station A → Station B → Destination
```

Where:
- each segment ≤ time limit  
- stations are valid (bike + dock available)  

---

### ⚠️ Risk-Aware Routing

Instead of returning only travel time:

```
Estimated: 14 min
```

The system evaluates feasibility:

```
Estimated: 14 min
Risk of exceeding limit: MEDIUM
```

---

## 🏗️ Architecture

```
GBFS API
   ↓
Data Fetcher
   ↓
Station Dataset
   ↓
Routing Engine
   ↓
FastAPI Backend
   ↓
Client (CLI / Mobile)
```

---

## 🧮 Algorithms15

- Graph traversal (BFS / A*)  
- Constrained shortest path  
- Delay-aware cost modeling  

### Cost Function

Instead of minimizing distance:

```
minimize distance
```

We optimize:

```
minimize (travel_time + delay + risk)
```

---

## 📊 Evaluation

The system is evaluated against a baseline:

### Baseline
- shortest-path routing  
- no delay modeling  

### Proposed Model
- delay-aware routing  
- constraint-based segmentation  

### Metrics
- % of trips exceeding time limit  
- average travel time  
- route feasibility  

---

## 🚀 Getting Started

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the API

```bash
uvicorn src.main:app --reload
```

### 3. Open API docs

```
http://127.0.0.1:8000/docs
```

---

## 🧪 Example Output

```
Start: Université Laval
Destination: Old Québec

Segment 1 → Station A (13.2 min)
Segment 2 → Station B (12.8 min)
Segment 3 → Destination (14.1 min)

Risk: LOW
```

---

## 📁 Project Structure

```
src/
  ├── api/        # FastAPI routes
  ├── data/       # GBFS data ingestion
  ├── routing/    # core algorithms
  ├── config/     # system parameters
  └── utils/      # helpers

scripts/
  └── demo_cli.py
```

---

## 🔮 Future Improvements

- Machine learning for delay prediction  
- Station availability forecasting  
- Time-dependent routing  
- Real-time re-routing  

---

## 💡 Why This Project Matters

This project demonstrates:

- real-time system design  
- constraint-based optimization  
- applied graph algorithms  
- modeling of real-world uncertainty  

It addresses a practical problem that existing navigation systems ignore.

---

## 📄 License

MIT License (or adjust depending on usage constraints)

---

## 👤 Author

Your Name  
