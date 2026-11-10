---
doc_id: inventario
title: Inventario, SKU y stock
category: operaciones
tags: [stock, sku, deposito, productos]
---

# Inventario, SKU y stock

> Documentación técnica ficticia, creada con fines educativos para el curso de AI Engineering.

## Identificación de productos

Cada producto del catálogo tiene un **SKU** con el formato `OLA-<LÍNEA>-<NÚMERO>`:

- `<LÍNEA>` es un código de 3 letras: `RIB` (Línea Ribera, comedor), `BEL` (Línea Bell, living), `ATL` (Línea Atelier, a medida) y `OUT` (outlet).
- `<NÚMERO>` es un correlativo de 4 dígitos con ceros a la izquierda.

Por ejemplo, `OLA-RIB-0042` es una mesa de la Línea Ribera. El SKU es único, no se reutiliza aunque el producto se discontinúe, y es el dato que se usa para vincular el producto con la tienda online.

## Stock disponible y reservado

Para cada producto, el sistema lleva tres cantidades:

- **Stock físico:** las unidades que hay en el depósito.
- **Stock reservado:** unidades comprometidas en ventas todavía no entregadas.
- **Stock disponible:** físico menos reservado. Es el único que se puede vender y el que se publica en la tienda online.

Una venta reserva stock al crearse; la entrega descuenta el stock físico y libera la reserva al mismo tiempo.

## Movimientos de stock

| Tipo | Quién lo registra | Efecto |
|---|---|---|
| Ingreso de mercadería | deposito | Suma stock físico; se vincula a una orden de compra |
| Fabricación terminada | taller | Suma stock físico de un producto fabricado en el taller propio |
| Entrega | deposito | Resta stock físico y libera la reserva |
| Ajuste por inventario | deposito, con aprobación de gerencia | Corrige diferencias de conteo |

Los ajustes por inventario mayores a 3 unidades quedan en estado `pendiente_aprobacion` hasta que alguien con rol `gerencia` los aprueba.

## Alertas de stock mínimo

Cada producto puede tener un **stock mínimo**. Todos los días a las 7:00 un proceso programado revisa el stock disponible y, si quedó por debajo del mínimo, envía un correo al rol `compras` con la lista de productos a reponer. Los productos de la Línea Atelier no tienen stock mínimo, porque se fabrican a pedido.

## Endpoints

- `GET /api/productos?sku=&linea=` — busca productos por SKU o línea.
- `GET /api/stock/:producto_id` — devuelve físico, reservado y disponible.
- `POST /api/stock/movimientos` — registra un movimiento; un ajuste sin permiso devuelve `403` con `AUTH-403-ROL`.
