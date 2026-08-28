# Stale entities and old entity ids

Home Assistant keeps an entity id after the integration that made it stops using it. This page explains why, and how to clean up.

## Why an entity id never changes on its own

Home Assistant builds an entity id once, when the entity registers for the first time. After that the id is stored, and no later change moves it. A device rename does not move it. An area change does not move it. An integration update does not move it.

Removing the integration does not move it either. Home Assistant remembers a removed entity for 30 days. If you add the integration again inside that window, the old entity id comes back, together with the name you gave the entity and the area you put it in.

This is good behavior. Your automations keep working across an update, a rename, and a reinstall.

It has one cost. An install created before version 0.0.12 keeps its old entity ids, in the `light.ampio_module_0xc9ae_osw_obwod_1` form. A fresh install of 0.0.12 uses `light.ampio_47846_obj_7` instead. Both work. Nothing forces you to change.

## Back up first

Do not start either procedure below without a backup. Both delete records that Home Assistant cannot rebuild from the Ampio server.

1. Open Settings, then System, then Backups.
2. Select "Create backup".
3. Wait for the backup to finish.

## Remove a few stale entities

Use this when Ampio Designer no longer has an object, and its entity is still listed. Such an entity shows the state `unavailable` or the label "restored".

1. Open Settings, then Devices and Services, then Entities.
2. Search for the entity.
3. Select it, then select the cog icon.
4. Select "Delete".

If the "Delete" button is not offered, the integration still creates that entity. Check Ampio Designer before you go further.

## Reset every Ampio entity id

Use this only to move an old install onto the current id scheme. The procedure deletes every Ampio entity record, so Home Assistant builds the ids again from scratch on the next start.

**Every automation, script, scene, and dashboard card that names an Ampio entity id stops working.** Write those ids down first, and plan to repoint them.

You need shell access to the Home Assistant host, through the SSH add-on or the Terminal add-on.

1. Take a backup, as described above.
2. Write down the Ampio entity ids your automations use.
3. Stop Home Assistant:

   ```sh
   ha core stop
   ```

4. Copy the entity registry, then remove every Ampio record from it:

   ```sh
   sudo cp /config/.storage/core.entity_registry /config/.storage/core.entity_registry.bak
   sudo jq '(.data.entities, .data.deleted_entities) |= map(select(.platform != "ampio"))' \
     /config/.storage/core.entity_registry > /tmp/registry.json
   sudo cp /tmp/registry.json /config/.storage/core.entity_registry
   ```

5. Start Home Assistant:

   ```sh
   ha core start
   ```

6. Open Settings, then Devices and Services, then Ampio. Confirm that the entity count matches what you had.
7. Repoint your automations at the new ids.

The `deleted_entities` list matters as much as the `entities` list. Leave the deleted records in place, and Home Assistant restores every old id on the next start.

### If something goes wrong

Stop Home Assistant, copy the backup file back, then start Home Assistant:

```sh
ha core stop
sudo cp /config/.storage/core.entity_registry.bak /config/.storage/core.entity_registry
ha core start
```

If the instance does not start at all, restore the full backup from Settings, System, Backups.

## What this does not touch

- Your Ampio configuration. The M-SERV holds it, and this integration only reads it.
- Your devices. Device names and areas live in a separate registry.
- Any other integration. The filter selects the `ampio` platform alone.
