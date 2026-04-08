#!/bin/bash

echo "Detecting removed fields..."

git diff HEAD~1 HEAD schema.txt > diff.txt

FIELDS=$(grep "^-" diff.txt | grep -v "^---" | awk '{print $1}' | sed 's/-//g' | sed 's/://g')

if [ -z "$FIELDS" ]; then
  echo "No fields removed"
  exit 0
fi

echo "Removed fields: $FIELDS"

for FIELD in $FIELDS
do
  echo "Running analyzer for field: $FIELD"
  python delete_dependency_check.py --schema schema.txt --field $FIELD

  if [ $? -ne 0 ]; then
    echo "❌ Breaking change detected for $FIELD"
    exit 1
  fi
done

echo "✅ No breaking issues"