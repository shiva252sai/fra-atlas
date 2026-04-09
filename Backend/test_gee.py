import ee
try:
    ee.Initialize()
    print("SUCCESS: GEE is successfully initialized!")
    print("Test Number:", ee.Number(100).getInfo())
except Exception as e:
    print("ERROR GEE Initialization Failed!", e)
