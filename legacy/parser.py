import csv


# 1. Define a class to represent a single genomic variant record
class Variant:
    def __init__(self, sample_id, chromosome, position, read_depth, quality_score, variant_type):
        self.sample_id = sample_id
        self.chromosome = chromosome
        self.position = int(position)
        self.read_depth = int(read_depth)
        self.quality_score = float(quality_score)
        self.variant_type = variant_type

    def is_high_quality(self):
        """Helper method to check if a variant meets quality thresholds."""
        return self.quality_score >= 30.0 and self.read_depth >= 10

    def __repr__(self):
        return f"<Variant {self.sample_id} | {self.chromosome}:{self.position} | Qual: {self.quality_score}>"


# 2. Define a class to parse the VCF/CSV file
class VCFParser:
    def __init__(self, file_path):
        self.file_path = file_path
        self.variants = []

    def load_data(self):
        """Reads CSV line-by-line and instantiates Variant objects with error handling."""
        records_loaded = 0
        skipped_records = 0

        with open(self.file_path, mode="r") as file:
            reader = csv.DictReader(file)
            for row in reader:
                try:
                    # Instantiate a Variant object from the row dictionary
                    var = Variant(
                        sample_id=row["Sample_ID"],
                        chromosome=row["Chromosome"],
                        position=row["Position"],
                        read_depth=row["Read_Depth"],
                        quality_score=row["Quality_Score"],
                        variant_type=row["Variant_Type"]
                    )
                    self.variants.append(var)
                    records_loaded += 1
                except (KeyError, ValueError) as e:
                    # Catch malformed or corrupted rows gracefully
                    skipped_records += 1

        print(f"✅ Loaded {records_loaded} valid variants into memory.")
        if skipped_records > 0:
            print(f"⚠️ Skipped {skipped_records} corrupted rows.")

# Quick local test when running parser.py directly
if __name__ == "__main__":
    parser = VCFParser("variants.csv")
    parser.load_data()
    
    # Inspect the first 3 loaded variant objects
    print("\nSample Object Output:")
    for v in parser.variants[:3]:
        print(f"Object: {v} | Pass Quality Check? {v.is_high_quality()}")