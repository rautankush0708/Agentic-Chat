"""SQLAlchemy models mirroring backend/app/schema/demand_forecasting_schema.json.

Column names/casing intentionally match the schema JSON exactly, because that JSON
is what gets embedded into the LLM prompt (see services/ai_data_query_service.py) and
the LLM's generated SQL is executed directly against this database. If a column here
doesn't match a name the schema JSON advertises, generated SQL referencing it is
rejected by ValidateColumns (the "zero hallucination" contract ported from the
original C# service).
"""

from .extensions import db


class Category(db.Model):
    __tablename__ = "Category"
    CategoryId = db.Column(db.Integer, primary_key=True)
    CategoryName = db.Column(db.String(100))
    IsActive = db.Column(db.Integer, default=1)
    IsDeleted = db.Column(db.Integer, default=0)


class Product(db.Model):
    __tablename__ = "Product"
    ProductId = db.Column(db.Integer, primary_key=True)
    SKU = db.Column(db.String(50))
    ProductName = db.Column(db.String(150))
    CategoryId = db.Column(db.Integer, db.ForeignKey("Category.CategoryId"))
    Brand = db.Column(db.String(100))
    UnitOfMeasure = db.Column(db.String(20))
    UnitPrice = db.Column(db.Float)
    LaunchDate = db.Column(db.DateTime)
    IsActive = db.Column(db.Integer, default=1)
    IsDeleted = db.Column(db.Integer, default=0)
    CreatedDate = db.Column(db.DateTime)


class Location(db.Model):
    __tablename__ = "Location"
    LocationId = db.Column(db.Integer, primary_key=True)
    LocationName = db.Column(db.String(150))
    LocationType = db.Column(db.String(20))
    Region = db.Column(db.String(100))
    City = db.Column(db.String(100))
    IsActive = db.Column(db.Integer, default=1)
    IsDeleted = db.Column(db.Integer, default=0)


class Channel(db.Model):
    __tablename__ = "Channel"
    ChannelId = db.Column(db.Integer, primary_key=True)
    ChannelName = db.Column(db.String(50))
    IsActive = db.Column(db.Integer, default=1)
    IsDeleted = db.Column(db.Integer, default=0)


class UserRoles(db.Model):
    __tablename__ = "UserRoles"
    RoleID = db.Column(db.Integer, primary_key=True)
    RoleName = db.Column(db.String(50))
    IsActive = db.Column(db.Integer, default=1)
    IsDeleted = db.Column(db.Integer, default=0)


class Staffs(db.Model):
    __tablename__ = "Staffs"
    StaffId = db.Column(db.Integer, primary_key=True)
    FirstName = db.Column(db.String(100))
    LastName = db.Column(db.String(100))
    RoleID = db.Column(db.Integer, db.ForeignKey("UserRoles.RoleID"))
    Region = db.Column(db.String(100))
    Email = db.Column(db.String(150))
    IsActive = db.Column(db.Integer, default=1)
    IsDeleted = db.Column(db.Integer, default=0)
    CreatedDate = db.Column(db.DateTime)


class SalesHistory(db.Model):
    __tablename__ = "SalesHistory"
    SalesHistoryId = db.Column(db.Integer, primary_key=True)
    ProductId = db.Column(db.Integer, db.ForeignKey("Product.ProductId"))
    LocationId = db.Column(db.Integer, db.ForeignKey("Location.LocationId"))
    ChannelId = db.Column(db.Integer, db.ForeignKey("Channel.ChannelId"))
    SaleDate = db.Column(db.DateTime)
    QuantitySold = db.Column(db.Integer)
    UnitPrice = db.Column(db.Float)
    Revenue = db.Column(db.Float)
    IsPromotion = db.Column(db.Integer, default=0)
    CreatedDate = db.Column(db.DateTime)


class Promotion(db.Model):
    __tablename__ = "Promotion"
    PromotionId = db.Column(db.Integer, primary_key=True)
    ProductId = db.Column(db.Integer, db.ForeignKey("Product.ProductId"))
    PromotionName = db.Column(db.String(150))
    StartDate = db.Column(db.DateTime)
    EndDate = db.Column(db.DateTime)
    DiscountPercent = db.Column(db.Float)
    PromotionType = db.Column(db.String(50))
    IsActive = db.Column(db.Integer, default=1)
    IsDeleted = db.Column(db.Integer, default=0)


class Inventory(db.Model):
    __tablename__ = "Inventory"
    InventoryId = db.Column(db.Integer, primary_key=True)
    ProductId = db.Column(db.Integer, db.ForeignKey("Product.ProductId"))
    LocationId = db.Column(db.Integer, db.ForeignKey("Location.LocationId"))
    OnHandQuantity = db.Column(db.Integer)
    InTransitQuantity = db.Column(db.Integer)
    SafetyStock = db.Column(db.Integer)
    ReorderPoint = db.Column(db.Integer)
    LastUpdated = db.Column(db.DateTime)


class DemandPatternClassification(db.Model):
    __tablename__ = "DemandPatternClassification"
    ClassificationId = db.Column(db.Integer, primary_key=True)
    ProductId = db.Column(db.Integer, db.ForeignKey("Product.ProductId"))
    LocationId = db.Column(db.Integer, db.ForeignKey("Location.LocationId"), nullable=True)
    PatternType = db.Column(db.String(30))
    ADI = db.Column(db.Float)
    CV2 = db.Column(db.Float)
    ClassifiedDate = db.Column(db.DateTime)


class ForecastRun(db.Model):
    __tablename__ = "ForecastRun"
    ForecastRunId = db.Column(db.Integer, primary_key=True)
    RunName = db.Column(db.String(150))
    ModelName = db.Column(db.String(50))
    RunDate = db.Column(db.DateTime)
    ForecastHorizonDays = db.Column(db.Integer)
    Status = db.Column(db.String(20))
    CreatedBy = db.Column(db.Integer, db.ForeignKey("Staffs.StaffId"))
    CreatedDate = db.Column(db.DateTime)


class ForecastDetail(db.Model):
    __tablename__ = "ForecastDetail"
    ForecastDetailId = db.Column(db.Integer, primary_key=True)
    ForecastRunId = db.Column(db.Integer, db.ForeignKey("ForecastRun.ForecastRunId"))
    ProductId = db.Column(db.Integer, db.ForeignKey("Product.ProductId"))
    LocationId = db.Column(db.Integer, db.ForeignKey("Location.LocationId"))
    ForecastDate = db.Column(db.DateTime)
    ForecastedQuantity = db.Column(db.Float)
    LowerBound = db.Column(db.Float)
    UpperBound = db.Column(db.Float)
    ConfidenceLevel = db.Column(db.Float)


class ForecastAccuracy(db.Model):
    __tablename__ = "ForecastAccuracy"
    ForecastAccuracyId = db.Column(db.Integer, primary_key=True)
    ForecastRunId = db.Column(db.Integer, db.ForeignKey("ForecastRun.ForecastRunId"))
    ProductId = db.Column(db.Integer, db.ForeignKey("Product.ProductId"))
    LocationId = db.Column(db.Integer, db.ForeignKey("Location.LocationId"))
    PeriodDate = db.Column(db.DateTime)
    ForecastedQuantity = db.Column(db.Float)
    ActualQuantity = db.Column(db.Float)
    AbsoluteError = db.Column(db.Float)
    MAPE = db.Column(db.Float)
    Bias = db.Column(db.Float)


class ReplenishmentOrder(db.Model):
    __tablename__ = "ReplenishmentOrder"
    OrderId = db.Column(db.Integer, primary_key=True)
    ProductId = db.Column(db.Integer, db.ForeignKey("Product.ProductId"))
    LocationId = db.Column(db.Integer, db.ForeignKey("Location.LocationId"))
    OrderDate = db.Column(db.DateTime)
    OrderQuantity = db.Column(db.Integer)
    ExpectedDeliveryDate = db.Column(db.DateTime)
    Status = db.Column(db.String(20))
    CreatedBy = db.Column(db.Integer, db.ForeignKey("Staffs.StaffId"))
