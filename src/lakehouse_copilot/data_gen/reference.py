"""Reference data for the generator: Indian cities, names, product catalog, festivals."""

from __future__ import annotations

from datetime import date

# (city, state, region, city_tier)
CITIES: list[tuple[str, str, str, int]] = [
    ("Mumbai", "Maharashtra", "West", 1),
    ("Delhi", "Delhi", "North", 1),
    ("Bengaluru", "Karnataka", "South", 1),
    ("Chennai", "Tamil Nadu", "South", 1),
    ("Hyderabad", "Telangana", "South", 1),
    ("Kolkata", "West Bengal", "East", 1),
    ("Pune", "Maharashtra", "West", 1),
    ("Ahmedabad", "Gujarat", "West", 1),
    ("Jaipur", "Rajasthan", "North", 2),
    ("Lucknow", "Uttar Pradesh", "North", 2),
    ("Chandigarh", "Chandigarh", "North", 2),
    ("Kochi", "Kerala", "South", 2),
    ("Coimbatore", "Tamil Nadu", "South", 2),
    ("Visakhapatnam", "Andhra Pradesh", "South", 2),
    ("Thiruvananthapuram", "Kerala", "South", 2),
    ("Surat", "Gujarat", "West", 2),
    ("Indore", "Madhya Pradesh", "Central", 2),
    ("Bhopal", "Madhya Pradesh", "Central", 2),
    ("Nagpur", "Maharashtra", "Central", 2),
    ("Bhubaneswar", "Odisha", "East", 2),
    ("Patna", "Bihar", "East", 2),
    ("Guwahati", "Assam", "East", 2),
    ("Madurai", "Tamil Nadu", "South", 3),
    ("Mysuru", "Karnataka", "South", 3),
    ("Mangaluru", "Karnataka", "South", 3),
    ("Nashik", "Maharashtra", "West", 3),
    ("Ranchi", "Jharkhand", "East", 3),
    ("Dehradun", "Uttarakhand", "North", 3),
    ("Varanasi", "Uttar Pradesh", "North", 3),
    ("Raipur", "Chhattisgarh", "Central", 3),
]

# Bigger cities get more customers.
CITY_TIER_WEIGHT = {1: 3.0, 2: 1.5, 3: 1.0}

FIRST_NAMES = [
    "Aarav",
    "Vivaan",
    "Aditya",
    "Vihaan",
    "Arjun",
    "Sai",
    "Reyansh",
    "Krishna",
    "Ishaan",
    "Rohan",
    "Karthik",
    "Rahul",
    "Vikram",
    "Suresh",
    "Ramesh",
    "Teja",
    "Venkat",
    "Harsha",
    "Manoj",
    "Anil",
    "Sanjay",
    "Pranav",
    "Nikhil",
    "Siddharth",
    "Abhishek",
    "Gaurav",
    "Ananya",
    "Diya",
    "Aadhya",
    "Saanvi",
    "Pari",
    "Ira",
    "Myra",
    "Kavya",
    "Priya",
    "Sneha",
    "Divya",
    "Lakshmi",
    "Meera",
    "Pooja",
    "Neha",
    "Swathi",
    "Keerthi",
    "Bhavana",
    "Anjali",
    "Deepika",
    "Revathi",
    "Shreya",
    "Nandini",
    "Aishwarya",
    "Fatima",
    "Imran",
    "Zoya",
    "Arif",
    "Joseph",
    "Mary",
    "Gurpreet",
    "Harpreet",
    "Simran",
    "Manpreet",
]

LAST_NAMES = [
    "Sharma",
    "Verma",
    "Gupta",
    "Singh",
    "Kumar",
    "Reddy",
    "Rao",
    "Naidu",
    "Iyer",
    "Iyengar",
    "Nair",
    "Menon",
    "Pillai",
    "Patel",
    "Shah",
    "Mehta",
    "Desai",
    "Joshi",
    "Kulkarni",
    "Deshpande",
    "Banerjee",
    "Chatterjee",
    "Mukherjee",
    "Das",
    "Bose",
    "Ghosh",
    "Mishra",
    "Tiwari",
    "Pandey",
    "Yadav",
    "Chowdary",
    "Rapuri",
    "Varma",
    "Shetty",
    "Hegde",
    "Khan",
    "Ahmed",
    "Fernandes",
    "D'Souza",
    "Gill",
]

# category -> (sub_categories, brands, (min_price, max_price) in INR, margin range)
CATEGORIES: dict[str, dict] = {
    "Electronics": {
        "sub_categories": ["Smartphones", "Laptops", "Headphones", "Smartwatches", "Televisions"],
        "brands": ["Samsung", "Xiaomi", "OnePlus", "Apple", "boAt", "Lenovo", "Sony", "Realme"],
        "price": (999, 89999),
        "margin": (0.08, 0.18),
    },
    "Fashion": {
        "sub_categories": ["Sarees", "Kurtas", "Jeans", "Footwear", "Ethnic Wear"],
        "brands": ["FabIndia", "Biba", "Manyavar", "Levi's", "Bata", "W", "Raymond"],
        "price": (299, 7999),
        "margin": (0.30, 0.55),
    },
    "Home & Kitchen": {
        "sub_categories": ["Cookware", "Mixer Grinders", "Bedsheets", "Decor Lights", "Storage"],
        "brands": ["Prestige", "Pigeon", "Bajaj", "Philips", "Milton", "Butterfly"],
        "price": (199, 14999),
        "margin": (0.20, 0.40),
    },
    "Beauty": {
        "sub_categories": ["Skincare", "Haircare", "Fragrances", "Makeup"],
        "brands": ["Himalaya", "Lakme", "Mamaearth", "Biotique", "Nykaa"],
        "price": (99, 2999),
        "margin": (0.35, 0.60),
    },
    "Grocery": {
        "sub_categories": ["Staples", "Dry Fruits", "Sweets", "Beverages", "Snacks"],
        "brands": ["Tata", "Aashirvaad", "Haldiram's", "Amul", "Fortune", "Britannia"],
        "price": (49, 1999),
        "margin": (0.08, 0.20),
    },
    "Books": {
        "sub_categories": ["Fiction", "Exam Prep", "Children", "Self Help"],
        "brands": ["Penguin", "Arihant", "HarperCollins", "Rupa"],
        "price": (149, 1499),
        "margin": (0.15, 0.35),
    },
    "Sports": {
        "sub_categories": ["Cricket", "Fitness", "Cycling", "Yoga"],
        "brands": ["SG", "Nivia", "Decathlon", "Cosco", "Yonex"],
        "price": (199, 19999),
        "margin": (0.20, 0.40),
    },
}

# Normal share of order lines per category.
CATEGORY_WEIGHT = {
    "Electronics": 0.18,
    "Fashion": 0.24,
    "Home & Kitchen": 0.16,
    "Beauty": 0.12,
    "Grocery": 0.16,
    "Books": 0.06,
    "Sports": 0.08,
}

# Categories that sell much more in the Diwali festive season.
FESTIVE_CATEGORY_BOOST = {"Electronics": 2.2, "Fashion": 1.8, "Home & Kitchen": 2.0, "Grocery": 1.5}

# Diwali (Lakshmi Puja) dates for the years we generate.
DIWALI_DATES = {2024: date(2024, 11, 1), 2025: date(2025, 10, 20)}
FESTIVE_DAYS_BEFORE = 21
FESTIVE_DAYS_AFTER = 2

# Canonical payment methods and their base share of orders.
PAYMENT_METHODS = {
    "UPI": 0.45,
    "COD": 0.20,
    "Credit Card": 0.13,
    "Debit Card": 0.10,
    "Wallet": 0.07,
    "Net Banking": 0.05,
}

# Messy raw spellings the source system sends; Silver maps them back to canonical names.
PAYMENT_METHOD_VARIANTS = {
    "UPI": ["UPI", "upi", "Upi ", "UPI"],
    "COD": ["COD", "cod", "Cash on Delivery", "COD"],
    "Credit Card": ["Credit Card", "CREDIT_CARD", "credit card"],
    "Debit Card": ["Debit Card", "DEBIT_CARD", "debit card"],
    "Wallet": ["Wallet", "wallet", "WALLET"],
    "Net Banking": ["Net Banking", "NETBANKING", "net banking"],
}

ORDER_STATUSES = {"Delivered": 0.82, "Shipped": 0.04, "Cancelled": 0.08, "Returned": 0.06}
CHANNELS = {"App": 0.65, "Web": 0.35}
