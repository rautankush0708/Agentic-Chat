"""Populates the SQLite database with a plausible slice of demand-forecasting demo
data so NL->SQL questions (today's/this month's demand, forecast accuracy, stockout
risk, demand pattern mix, open replenishment orders, etc.) return real results.

Dates are generated relative to "now" at seed time so date-based questions like
"today" / "this week" / "this month" behave correctly whenever the app is run.

Run with: python -m app.seed  (from backend/, inside the venv)
"""

import math
import random
from datetime import datetime, timedelta

from . import create_app
from .extensions import db
from .models import (
    Category,
    Channel,
    DemandPatternClassification,
    ForecastAccuracy,
    ForecastDetail,
    ForecastRun,
    Inventory,
    Location,
    Product,
    Promotion,
    ReplenishmentOrder,
    SalesHistory,
    Staffs,
    UserRoles,
)

random.seed(7)
NOW = datetime.now()


def days_ago(n):
    return NOW - timedelta(days=n)


def days_ahead(n):
    return NOW + timedelta(days=n)


PRODUCT_CATALOG = [
    ("Beverages", "Sparkling Water 500ml", "AquaFizz"),
    ("Beverages", "Cold Brew Coffee 250ml", "RoastCo"),
    ("Beverages", "Orange Juice 1L", "SunSip"),
    ("Snacks", "Sea Salt Potato Chips 150g", "CrispCo"),
    ("Snacks", "Trail Mix 200g", "NutriBite"),
    ("Snacks", "Chocolate Cookies 300g", "SweetLeaf"),
    ("Personal Care", "Shampoo 400ml", "PureGlow"),
    ("Personal Care", "Toothpaste 100g", "BrightSmile"),
    ("Personal Care", "Hand Sanitizer 100ml", "CleanHands"),
    ("Home Care", "Dish Soap 500ml", "SudsUp"),
    ("Home Care", "Laundry Detergent 1kg", "FreshWash"),
    ("Apparel", "Cotton T-Shirt", "UrbanThread"),
    ("Apparel", "Running Shoes", "StrideFit"),
    ("Electronics", "Wireless Earbuds", "SoundWave"),
    ("Electronics", "Power Bank 10000mAh", "ChargeUp"),
    ("Electronics", "Bluetooth Speaker", "SoundWave"),
    ("Seasonal", "Holiday Gift Box", "FestiveCo"),
    ("Seasonal", "Summer Cooling Towel", "CoolBreeze"),
]

DEMAND_PATTERNS = ["Smooth", "Erratic", "Intermittent", "Lumpy", "Seasonal", "Trend", "New Launch"]
MODEL_NAMES = ["Prophet", "ARIMA", "ETS", "LSTM", "Moving Average"]


def run():
    app = create_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

        # ---------------- Master data ----------------
        category_names = sorted({c for c, _, _ in PRODUCT_CATALOG})
        categories = {name: Category(CategoryName=name, IsActive=1, IsDeleted=0) for name in category_names}
        db.session.add_all(categories.values())
        db.session.flush()

        locations = [
            Location(LocationName="North DC", LocationType="DC", Region="North", City="Chicago", IsActive=1, IsDeleted=0),
            Location(LocationName="South DC", LocationType="DC", Region="South", City="Dallas", IsActive=1, IsDeleted=0),
            Location(LocationName="West DC", LocationType="DC", Region="West", City="Los Angeles", IsActive=1, IsDeleted=0),
            Location(LocationName="Downtown Store", LocationType="Store", Region="North", City="Chicago", IsActive=1, IsDeleted=0),
            Location(LocationName="Riverside Store", LocationType="Store", Region="South", City="Austin", IsActive=1, IsDeleted=0),
            Location(LocationName="Harbor Store", LocationType="Store", Region="West", City="San Diego", IsActive=1, IsDeleted=0),
        ]
        db.session.add_all(locations)
        db.session.flush()

        channels = [
            Channel(ChannelName="Retail", IsActive=1, IsDeleted=0),
            Channel(ChannelName="Online", IsActive=1, IsDeleted=0),
            Channel(ChannelName="Wholesale", IsActive=1, IsDeleted=0),
        ]
        db.session.add_all(channels)
        db.session.flush()

        roles = [
            UserRoles(RoleID=1, RoleName="Admin", IsActive=1, IsDeleted=0),
            UserRoles(RoleID=2, RoleName="Demand Planner", IsActive=1, IsDeleted=0),
            UserRoles(RoleID=3, RoleName="Supply Planner", IsActive=1, IsDeleted=0),
            UserRoles(RoleID=4, RoleName="Category Manager", IsActive=1, IsDeleted=0),
            UserRoles(RoleID=5, RoleName="Analyst", IsActive=1, IsDeleted=0),
            UserRoles(RoleID=6, RoleName="Executive", IsActive=1, IsDeleted=0),
        ]
        db.session.add_all(roles)
        db.session.flush()

        staff = [
            Staffs(FirstName="Alicia", LastName="Nguyen", RoleID=1, Region="Global", Email="alicia.nguyen@demandco.demo", IsActive=1, IsDeleted=0, CreatedDate=days_ago(400)),
            Staffs(FirstName="Marcus", LastName="Reyes", RoleID=2, Region="North", Email="marcus.reyes@demandco.demo", IsActive=1, IsDeleted=0, CreatedDate=days_ago(400)),
            Staffs(FirstName="Priya", LastName="Menon", RoleID=3, Region="South", Email="priya.menon@demandco.demo", IsActive=1, IsDeleted=0, CreatedDate=days_ago(400)),
            Staffs(FirstName="Ethan", LastName="Cole", RoleID=4, Region="West", Email="ethan.cole@demandco.demo", IsActive=1, IsDeleted=0, CreatedDate=days_ago(400)),
            Staffs(FirstName="Sara", LastName="Kim", RoleID=5, Region="Global", Email="sara.kim@demandco.demo", IsActive=1, IsDeleted=0, CreatedDate=days_ago(400)),
        ]
        db.session.add_all(staff)
        db.session.flush()
        planner = staff[1]

        products = []
        for cat_name, name, brand in PRODUCT_CATALOG:
            launch = days_ago(random.randint(30, 900))
            p = Product(
                SKU=f"SKU-{1000 + len(products)}", ProductName=name, CategoryId=categories[cat_name].CategoryId,
                Brand=brand, UnitOfMeasure="EA", UnitPrice=round(random.uniform(3, 120), 2),
                LaunchDate=launch, IsActive=1, IsDeleted=0, CreatedDate=launch,
            )
            products.append(p)
        db.session.add_all(products)
        db.session.flush()

        # ---------------- Demand pattern classification ----------------
        for p in products:
            recent = (NOW - p.LaunchDate).days < 60
            pattern = "New Launch" if recent else random.choice(DEMAND_PATTERNS[:-1])
            db.session.add(DemandPatternClassification(
                ProductId=p.ProductId, LocationId=None, PatternType=pattern,
                ADI=round(random.uniform(1.0, 4.0), 2), CV2=round(random.uniform(0.1, 1.5), 2),
                ClassifiedDate=days_ago(random.randint(1, 20)),
            ))

        # ---------------- Sales history: last 180 days ----------------
        for p in products:
            base_demand = random.uniform(5, 60)
            trend = random.uniform(-0.03, 0.05)
            for day_offset in range(180, -1, -1):
                sale_date = days_ago(day_offset)
                if sale_date < p.LaunchDate:
                    continue
                # skip some days for intermittent-feeling data
                if random.random() < 0.12:
                    continue
                seasonal = 1 + 0.25 * math.sin((180 - day_offset) / 30.0)
                qty = max(0, round(base_demand * seasonal * (1 + trend * (180 - day_offset) / 30.0) * random.uniform(0.6, 1.4)))
                if qty == 0:
                    continue
                location = random.choice(locations)
                channel = random.choice(channels)
                unit_price = p.UnitPrice * random.uniform(0.9, 1.0)
                db.session.add(SalesHistory(
                    ProductId=p.ProductId, LocationId=location.LocationId, ChannelId=channel.ChannelId,
                    SaleDate=sale_date, QuantitySold=qty, UnitPrice=round(unit_price, 2),
                    Revenue=round(qty * unit_price, 2), IsPromotion=0, CreatedDate=sale_date,
                ))
        db.session.flush()

        # ---------------- Promotions ----------------
        for p in random.sample(products, 6):
            start = days_ago(random.randint(0, 10))
            end = start + timedelta(days=random.randint(5, 14))
            db.session.add(Promotion(
                ProductId=p.ProductId, PromotionName=f"{p.ProductName} Spotlight Deal",
                StartDate=start, EndDate=end, DiscountPercent=random.choice([10, 15, 20, 25]),
                PromotionType=random.choice(["Discount", "BOGO", "Bundle"]), IsActive=1, IsDeleted=0,
            ))

        # ---------------- Inventory snapshot ----------------
        for p in products:
            for loc in locations:
                on_hand = random.randint(0, 400)
                safety = random.randint(30, 100)
                reorder = safety + random.randint(10, 50)
                db.session.add(Inventory(
                    ProductId=p.ProductId, LocationId=loc.LocationId, OnHandQuantity=on_hand,
                    InTransitQuantity=random.randint(0, 100), SafetyStock=safety, ReorderPoint=reorder,
                    LastUpdated=days_ago(random.randint(0, 2)),
                ))

        # ---------------- Forecast runs, details, accuracy ----------------
        forecast_runs = []
        for i, model in enumerate(MODEL_NAMES):
            run_date = days_ago(len(MODEL_NAMES) - i)
            fr = ForecastRun(
                RunName=f"{model} Weekly Run {run_date.strftime('%Y-%m-%d')}", ModelName=model,
                RunDate=run_date, ForecastHorizonDays=30, Status="Completed",
                CreatedBy=planner.StaffId, CreatedDate=run_date,
            )
            forecast_runs.append(fr)
        db.session.add_all(forecast_runs)
        db.session.flush()
        latest_run = forecast_runs[-1]

        for p in products:
            avg_recent = 20
            recent_sales = SalesHistory.query.filter_by(ProductId=p.ProductId).order_by(SalesHistory.SaleDate.desc()).limit(14).all()
            if recent_sales:
                avg_recent = sum(s.QuantitySold for s in recent_sales) / len(recent_sales)

            for horizon_day in range(1, 31):
                forecast_date = days_ahead(horizon_day)
                for loc in random.sample(locations, 3):
                    predicted = max(0, avg_recent * random.uniform(0.85, 1.15))
                    db.session.add(ForecastDetail(
                        ForecastRunId=latest_run.ForecastRunId, ProductId=p.ProductId, LocationId=loc.LocationId,
                        ForecastDate=forecast_date, ForecastedQuantity=round(predicted, 1),
                        LowerBound=round(predicted * 0.75, 1), UpperBound=round(predicted * 1.25, 1),
                        ConfidenceLevel=0.95,
                    ))

            for fr in forecast_runs:
                for loc in random.sample(locations, 2):
                    period = fr.RunDate
                    actual = max(0, avg_recent * random.uniform(0.7, 1.3))
                    forecasted = max(0, avg_recent * random.uniform(0.7, 1.3))
                    abs_error = abs(forecasted - actual)
                    mape = round((abs_error / actual) * 100, 2) if actual else 0.0
                    bias = round(forecasted - actual, 2)
                    db.session.add(ForecastAccuracy(
                        ForecastRunId=fr.ForecastRunId, ProductId=p.ProductId, LocationId=loc.LocationId,
                        PeriodDate=period, ForecastedQuantity=round(forecasted, 1), ActualQuantity=round(actual, 1),
                        AbsoluteError=round(abs_error, 1), MAPE=mape, Bias=bias,
                    ))

        # ---------------- Replenishment orders ----------------
        for _ in range(40):
            p = random.choice(products)
            loc = random.choice(locations)
            order_date = days_ago(random.randint(0, 30))
            status = random.choice(["Pending", "Shipped", "Delivered", "Delivered", "Cancelled"])
            expected = order_date + timedelta(days=random.randint(3, 14))
            db.session.add(ReplenishmentOrder(
                ProductId=p.ProductId, LocationId=loc.LocationId, OrderDate=order_date,
                OrderQuantity=random.randint(50, 500), ExpectedDeliveryDate=expected, Status=status,
                CreatedBy=random.choice(staff).StaffId,
            ))

        db.session.commit()
        print("Seed complete.")


if __name__ == "__main__":
    run()
