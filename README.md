# **MCP Location Finder for Puch AI**

This is a **custom MCP server** for Puch AI that helps users find **nearby places** such as:

* 🏥 **Doctors & Hospitals**
* ☕ **Cafes & Restaurants**
* 🛒 **Grocery Stores & Markets**
* ⭐ **Places with Reviews** (fetch ratings & feedback)

The tool uses the **Model Context Protocol (MCP)** to connect Puch AI to location services, giving the AI the ability to recommend and review places in real-time.

---

## **Features**

### 🌍 Location Finder Tool

* Detects **user’s current location** (with permission)
* Finds **nearby doctors, cafes, restaurants, grocery stores, and more**
* Fetches **reviews, ratings, and contact details** of the place
* Returns results in a **clean, AI-friendly format**

### 🔐 Built-in Authentication

* **Bearer token authentication** (required by Puch AI)
* Token validation tool that returns your registered number

---

## **Quick Setup Guide**

### **Step 1: Install Dependencies**

Make sure you have **Python 3.11 or higher** installed. Then:

```bash
# Create virtual environment
uv venv

# Install all required packages
uv sync

# Activate the environment
source .venv/bin/activate  # Mac/Linux
# OR
.venv\Scripts\activate     # Windows
```

---

### **Step 2: Set Up Environment Variables**

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` and add your details:

```env
AUTH_TOKEN=your_secret_token_here
MY_NUMBER=919876543210
GOOGLE_API_KEY=your_google_places_api_key_here
```

> **AUTH\_TOKEN** → Secret token for authentication (used by Puch AI)
> **MY\_NUMBER** → Your WhatsApp number in `{country_code}{number}` format
> **GOOGLE\_API\_KEY** → API key for fetching places and reviews from Google Places API

---

### **Step 3: Run the Server**

```bash
cd mcp-location-finder
python mcp_starter.py
```

You’ll see:

```
🚀 Starting MCP server on http://0.0.0.0:8086
```

---

### **Step 4: Make It Public (Required by Puch)**

#### Option A: Using ngrok (Recommended)

1. Install ngrok → [https://ngrok.com/download](https://ngrok.com/download)
2. Get your authtoken from [https://dashboard.ngrok.com/get-started/your-authtoken](https://dashboard.ngrok.com/get-started/your-authtoken)
3. Run:

```bash
ngrok config add-authtoken YOUR_AUTHTOKEN
ngrok http 8086
```

#### Option B: Deploy to Cloud

You can also deploy to:

* Railway
* Render
* Heroku
* DigitalOcean App Platform

---

## **How to Connect with Puch AI**

1. Open Puch AI in your browser
2. Start a new conversation
3. Use the connect command:

```plaintext
/mcp connect https://your-domain.ngrok.app/mcp your_secret_token_here
```

---

## **Adding New Location Categories**

To add more categories (e.g., gyms, pharmacies, ATMs), modify the search parameters in `location_finder.py`:

```python
CATEGORIES = ["doctor", "cafe", "restaurant", "grocery", "pharmacy", "atm"]
```

---

## **Technical Details**

* **Protocol:** Model Context Protocol (MCP)
* **Data Source:** Google Places API / OpenStreetMap
* **Auth:** Bearer Token
* **Spec:** JSON-RPC 2.0 compliant

---

## **Getting Help**

* 📖 **Docs:** [https://puch.ai/mcp](https://puch.ai/mcp)
* 💬 **Discord:** [https://discord.gg/VMCnMvYx](https://discord.gg/VMCnMvYx)
* 📲 **Puch WhatsApp:** +91 99988 81729

---

**#BuildWithPuch** 🚀

This project brings real-world **location awareness** to Puch AI, enabling it to recommend and review nearby places instantly.

---

Do you want me to also **add usage examples** showing exactly how a Puch AI user could type `/find doctor` and get results from your tool? That would make your README even stronger for the hackathon judges.
