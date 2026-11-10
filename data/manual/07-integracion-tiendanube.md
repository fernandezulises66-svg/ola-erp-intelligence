---
doc_id: tiendanube
title: Integración con la tienda online (Tiendanube)
category: integraciones
tags: [tiendanube, webhooks, ecommerce, pedidos]
---

# Integración con la tienda online (Tiendanube)

> Documentación técnica ficticia, creada con fines educativos para el curso de AI Engineering.

## Qué se sincroniza

| Dato | Dirección | Momento |
|---|---|---|
| Productos (nombre, precio, fotos) | ERP → tienda | Al editar el producto en el ERP |
| Stock disponible | ERP → tienda | Cada vez que cambia el stock disponible |
| Pedidos pagados | tienda → ERP | Cuando Tiendanube confirma el pago |

El vínculo entre un producto del ERP y uno de la tienda es el **SKU**.

## De pedido a venta

Un pedido de la tienda online **solo se convierte en venta del ERP cuando el pago está acreditado**. Tiendanube envía el webhook `order/paid` a `POST /api/integraciones/tiendanube/webhook`; los pedidos pendientes de pago o cancelados no generan ninguna venta ni reservan stock.

Al recibir un pedido pagado, el ERP:

1. Valida la firma del webhook con `TN_WEBHOOK_SECRET`. Si no coincide, responde `401` con el código `TN-401-FIRMA` y descarta el pedido.
2. Busca o crea el cliente a partir del correo del comprador.
3. Crea la venta con el usuario técnico **"Tienda online"** (rol `tienda_online`), punto de venta `5` y condición `contado`.
4. Emite automáticamente la factura electrónica, igual que cualquier otra venta.
5. Responde `200` a Tiendanube.

## Idempotencia

Tiendanube puede reenviar el mismo webhook si no recibe respuesta a tiempo. Para no duplicar ventas, el ERP guarda el identificador del pedido (`order_id`) en la columna `tiendanube_order_id`, con un índice único. Si llega un pedido ya procesado, responde `200` sin crear nada nuevo.

## Errores de la integración

| Código | Significado y solución |
|---|---|
| `TN-401-FIRMA` | La firma del webhook no es válida. Verificar que `TN_WEBHOOK_SECRET` coincida con el configurado en la app de Tiendanube |
| `TN-422-SKU` | Un producto del pedido tiene un SKU que no existe en el ERP. Crear o corregir el SKU y reprocesar el pedido desde la pantalla de integraciones |
| `TN-409-STOCK` | El pedido pagado no tiene stock disponible en el ERP (se vendió en el showroom al mismo tiempo). El pedido queda en la bandeja de "pedidos con conflicto" para resolverlo a mano |

Los pedidos con error quedan registrados con el cuerpo completo del webhook, así se pueden reprocesar sin pedirle a Tiendanube que los reenvíe.
