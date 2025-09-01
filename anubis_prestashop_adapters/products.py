import base64
from io import BytesIO
import requests
from anubis_core.features.product.ports import IProductAdapter
from anubis_core.features.product.models import CoreProduct
import xml.etree.ElementTree as ET

class PrestaShopProductAdapter(IProductAdapter):
    def __init__(self, 
                 base_url: str, 
                 api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            'Content-Type': 'application/xml',
            'Authorization': f"Basic {self.api_key}"
        }



    def _get_auth(self):
        return ( self.api_key,"")  # ❌ Esto es para headers, no para auth


    def get_product(self, id_product: int) -> CoreProduct:
        url = f"{self.base_url}/api/products/{id_product}"
        response = requests.get(url, auth=self._get_auth())
        response.raise_for_status()
        data = response.json()['product']
        return CoreProduct.from_dict(data)

    def create_product(self, product: CoreProduct) -> CoreProduct:
        
        url = f"{self.base_url}/api/products"
        payload = self._mapea_producto_core(product)                
        response = requests.post(url, data=payload, auth=self._get_auth())
        response.raise_for_status()
        producto_creado = self._mapea_producto_presta(response.text)

        url = f"{self.base_url}/api/images/products/{producto_creado.id}"
        i = 1
        for img64 in product.images_base64:    
            image_data = base64.b64decode(img64)        
            image_file = BytesIO(image_data)
            image_file_name = f"product_{producto_creado.id}_{i}.jpg"
            files = {
                        "image": (image_file_name, image_file, "image/jpeg")
                    }
            response = requests.post(url, auth=self._get_auth(), files=files)
            response.raise_for_status()
            i+=1

        url = f"{self.base_url}/api/stock_availables?filter[id_product]={producto_creado.id}"
        response = requests.get(url, auth=self._get_auth())
        tree = ET.fromstring(response.content)
        stock_node = tree.find(".//stock_available")
        stock_href = stock_node.attrib["{http://www.w3.org/1999/xlink}href"]
        stock_id = stock_node.attrib["id"]
        response = requests.get(stock_href, auth=self._get_auth())
        stock_xml = ET.fromstring(response.content)
        stock_xml.find(".//quantity").text = str(1)
        # stock_xml.find("//quantity").text = str(1)
        updated_xml = ET.tostring(stock_xml, encoding="utf-8")
        response = requests.put(url, auth=self._get_auth(), data=updated_xml)
        response.raise_for_status()

        return producto_creado

    def send_product(self, product: CoreProduct) -> CoreProduct:
        url = f"{self.base_url}/api/products/{product.id}"
        payload = product.to_dict()
        response = requests.put(url, json={'product': payload}, auth=self._get_auth())
        response.raise_for_status()
        updated = response.json()['product']
        return CoreProduct.from_dict(updated)

    def get_or_create_tag_id(self, tag_name: str) -> int:
        # Buscar tag
        url = f"{self.base_url}/api/tags?filter[name]={tag_name}"
        response = requests.get(url, auth=self._get_auth())
        response.raise_for_status()
        tags = response.json().get('tags', [])
        if tags:
            return int(tags[0]['id'])

        # Crear tag
        url = f"{self.base_url}/api/tags"
        payload = {'tag': {'name': tag_name}}
        response = requests.post(url, json=payload, auth=self._get_auth())
        response.raise_for_status()
        return int(response.json()['tag']['id'])

    def search_id(self, page: int, rows: int, *args, **kwargs) -> list[str]:
        url = f"{self.base_url}/api/products?limit={rows}&offset={(page - 1) * rows}"
        response = requests.get(url, auth=self._get_auth())
        response.raise_for_status()
        products = response.json().get('products', [])
        return [str(p['id']) for p in products]
    
    def _mapea_producto_presta(self, producto_odoo: str) -> CoreProduct:
        tree = ET.fromstring(producto_odoo)
        product_node = tree.find("product")

        def get_text(path):
            node = product_node.find(path)
            return node.text if node is not None else None

        def get_lang_text(path, lang_id="1"):
            node = product_node.find(f"{path}/language[@id='{lang_id}']")
            return node.text if node is not None else None

        # Extraer campos
        core = CoreProduct(
            id=int(get_text("id")) if get_text("id") else None,
            name=get_lang_text("name"),
            price=float(get_text("price") or 0.0),
            price_cost=float(get_text("wholesale_price") or 0.0),
            tax_id=float(get_text("id_tax_rules_group") or 0.0),
            default_code=get_text("reference"),
            barcode=get_text("ean13"),
            ecommerce_description=get_text("description"),
            store_description=get_text("description_short"),
            categories=[
                cat.find("id").text
                for cat in product_node.findall("associations/categories/category")
                if cat.find("id") is not None
            ],
            tags=[]  # Prestashop no tiene tags nativos, se puede mapear desde features si lo necesitas
        )

        return core

    
    def _mapea_producto_core(self, product: CoreProduct) -> str:
        prestashop = ET.Element("prestashop")
        prod = ET.SubElement(prestashop, "product")

        # Nombre del producto (idioma 1 = español por defecto)
        name = ET.SubElement(prod, "name")
        lang = ET.SubElement(name, "language", id="1")
        lang.text = product.name or "Producto sin nombre"

        # Precio de venta
        ET.SubElement(prod, "price").text = str(product.price or 0.0)

        # Precio de costo
        ET.SubElement(prod, "wholesale_price").text = str(product.price_cost or 0.0)

        # Impuestos
        ET.SubElement(prod, "id_tax_rules_group").text = str(int(product.tax_id or 1))

        # Código interno
        ET.SubElement(prod, "reference").text = product.default_code or ""

        # Código de barras
        ET.SubElement(prod, "ean13").text = product.barcode or ""

        # Descripciones
        ET.SubElement(prod, "description").text = product.ecommerce_description or ""
        ET.SubElement(prod, "description_short").text = product.store_description or ""

        # Activar producto
        ET.SubElement(prod, "active").text = "1"

        # Categorías (asociaciones)
        if product.categories:
            associations = ET.SubElement(prod, "associations")
            cats = ET.SubElement(associations, "categories")
            for cat_id in product.categories:
                cat = ET.SubElement(cats, "category")
                ET.SubElement(cat, "id").text = str(cat_id)

        # Puedes agregar más campos según lo que Prestashop permita

        # Convertir a string
        return ET.tostring(prestashop, encoding="unicode")
