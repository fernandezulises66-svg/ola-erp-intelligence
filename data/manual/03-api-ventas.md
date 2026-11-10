---
doc_id: api-ventas
title: API de ventas y presupuestos
category: api
tags: [ventas, presupuestos, endpoints, facturacion]
---

# API de ventas y presupuestos

> Documentación técnica ficticia, creada con fines educativos para el curso de AI Engineering.

## Flujo comercial

Una operación comercial pasa por estos estados:

`presupuesto` → `venta_pendiente` → `facturada` → `entregada`

Una venta también puede pasar a `anulada` desde cualquier estado anterior a `entregada`. Una venta `facturada` no se anula: se emite una nota de crédito.

## Endpoints principales

| Método y ruta | Rol mínimo | Descripción |
|---|---|---|
| `POST /api/presupuestos` | vendedor | Crea un presupuesto. Validez: 15 días corridos |
| `POST /api/presupuestos/:id/convertir` | vendedor | Convierte un presupuesto vigente en venta |
| `POST /api/ventas` | vendedor | Crea una venta directa, sin presupuesto previo |
| `GET /api/ventas?desde=&hasta=&estado=` | vendedor | Lista ventas con filtros, paginadas de a 50 |
| `POST /api/ventas/:id/facturar` | vendedor | Solicita el CAE a AFIP y emite la factura electrónica |
| `POST /api/ventas/:id/anular` | encargado_local | Anula una venta del día que todavía no fue facturada |

## Cuerpo de una venta

```json
{
  "cliente_id": 381,
  "punto_venta": 3,
  "tipo_comprobante": "B",
  "condicion_venta": "contado",
  "items": [
    {"producto_id": 77, "cantidad": 1, "precio_unitario": 420000.00}
  ],
  "observaciones": "Entrega en City Bell"
}
```

- `tipo_comprobante`: `A` si el cliente es responsable inscripto; `B` para consumidor final, monotributista o exento.
- `condicion_venta`: `contado`, `cuenta_corriente` o `seña`. Las ventas de muebles a medida se crean siempre con `seña` y requieren cobrar el 50 % al confirmar.
- `punto_venta`: `3` para el showroom y `5` para la tienda online.

Al crear la venta, el sistema **reserva** el stock de cada ítem. La reserva se libera si la venta se anula.

## Errores de la API de ventas

| Código | HTTP | Significado y solución |
|---|---|---|
| `VTA-409-STOCK` | 409 | No hay stock disponible para reservar uno de los ítems. Revisar el inventario o crear la venta como mueble a medida |
| `VTA-410-PRES` | 410 | El presupuesto venció (pasaron más de 15 días). Hay que actualizar los precios y generar uno nuevo |
| `VTA-422-CLI` | 422 | Al cliente le falta la condición frente al IVA, obligatoria para facturar |
| `VTA-409-FACT` | 409 | Se intentó anular una venta ya facturada. Corresponde emitir una nota de crédito |

Los errores de facturación devueltos por AFIP (por ejemplo, `ERR-AFIP-102`) se explican en la guía de integración con AFIP.
